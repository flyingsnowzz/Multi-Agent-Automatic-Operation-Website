#!/usr/bin/env python3
"""Publish Worker — SEO + 配图 + CMS 发布"""

import argparse, asyncio, json, os, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
from scripts.redis_pipeline import (get_redis, setup_streams, STREAM_PUBLISH,
    GROUP_PUBLISH, ack_message, recover_pending)
import redis.asyncio as redis

CONSUMER = f"publish-{os.getpid()}"


def env_flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_args():
    parser = argparse.ArgumentParser(description="Consume publish stream and send articles to CMS.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--publish", action="store_true", help="真实发布；仍要求 CMS_ENABLE_REAL_PUBLISH=true")
    mode.add_argument("--dry-run", action="store_true", help="只做发布预检，不请求 CMS 写入")
    parser.add_argument("--once", action="store_true", help="只处理一条消息后退出")
    parser.add_argument("--max-messages", type=int, default=0, help="最多处理 N 条消息，0 表示持续运行")
    parser.add_argument("--block-ms", type=int, default=5000, help="Redis 阻塞读取毫秒数")
    return parser.parse_args()


def resolve_dry_run(args):
    if args.publish:
        return False
    if args.dry_run:
        return True
    return env_flag("PUBLISH_DRY_RUN", True)


def preflight_publish_config(dry_run):
    if dry_run:
        print(f"[{CONSUMER}] 发布模式: dry-run")
        return

    missing = []
    if not env_flag("CMS_ENABLE_REAL_PUBLISH", False):
        missing.append("CMS_ENABLE_REAL_PUBLISH=true")
    if not (os.environ.get("CMS_API_URL") or os.environ.get("CMS_BASE_URL")):
        missing.append("CMS_API_URL")
    auth_ok = bool(os.environ.get("CMS_API_KEY") or (os.environ.get("CMS_USERNAME") and os.environ.get("CMS_PASSWORD")))
    if not auth_ok:
        missing.append("CMS_API_KEY 或 CMS_USERNAME/CMS_PASSWORD")
    if missing:
        raise RuntimeError("真实发布配置不完整: " + ", ".join(missing))
    print(f"[{CONSUMER}] 发布模式: real publish")


async def fill_article_content(item):
    if item.get("content_md") or item.get("content") or item.get("description") or not item.get("article_id"):
        return item
    try:
        import aiomysql
        pool = await aiomysql.create_pool(
            host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
            db=os.environ["MYSQL_DATABASE"], charset="utf8mb4", minsize=1, maxsize=1)
        async with pool.acquire() as c:
            async with c.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT title, description, original_url FROM crawler_news_main WHERE id=%s LIMIT 1",
                    (item.get("article_id"),))
                row = await cur.fetchone()
        pool.close(); await pool.wait_closed()
        if row:
            item["title"] = item.get("title") or row.get("title", "")
            item["description"] = row.get("description", "") or ""
            item["content"] = item["description"]
            item["source_url"] = item.get("source_url") or row.get("original_url", "")
    except Exception as e:
        print(f"[{CONSUMER}] 正文回填失败: {e}")
    return item


def slugify(title):
    import unicodedata
    s = unicodedata.normalize("NFKD", title)
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[-\s]+", "-", s)[:60].strip("-") or "article"


async def main():
    args = parse_args()
    dry_run = resolve_dry_run(args)
    try:
        preflight_publish_config(dry_run)
    except RuntimeError as e:
        print(f"[{CONSUMER}] {e}", file=sys.stderr)
        return 2

    r = await get_redis()
    await setup_streams(r)
    await recover_pending(r, STREAM_PUBLISH, GROUP_PUBLISH, CONSUMER)
    print(f"[{CONSUMER}] 启动")
    processed_count = 0

    while True:
        try:
            msgs = await r.xreadgroup(GROUP_PUBLISH, CONSUMER,
                                       {STREAM_PUBLISH: ">"}, count=1, block=args.block_ms)
        except redis.ResponseError:
            await asyncio.sleep(5); continue

        if not msgs:
            if args.once or (args.max_messages and processed_count >= args.max_messages):
                break
            continue

        for stream, entries in msgs:
            for msg_id, fields in entries:
                try:
                    item = json.loads(fields.get("data", "{}"))
                except Exception:
                    await ack_message(r, STREAM_PUBLISH, GROUP_PUBLISH, msg_id)
                    continue

                item = await fill_article_content(item)
                title = item.get("title", "")
                content = item.get("edited_content_md") or item.get("content_md") or item.get("content") or item.get("description", "")
                try:
                    # SEO + Image
                    image_prompt = f"新闻配图: {title}"
                    from agents.seo_agent import SEOAgent
                    s = await SEOAgent().execute(keyword_mode="v2",
                        article={"title": title, "content_md": content, "meta_description": "", "slug": ""},
                        topic=item, page_info={"slug": slugify(title), "category": "news"}, dry_run=True)
                    seo = s if isinstance(s, dict) else {}

                    # Image
                    from agents.image_agent.tools.coze_image_provider import CozeImageProvider
                    cp = CozeImageProvider()
                    try:
                        img = await cp.generate(prompt=image_prompt, n=1)
                    finally:
                        await cp.close()
                    image_item = (img.get("images") or [{}])[0] if img.get("success") and img.get("images") else {}
                    image_url = image_item.get("url", "")
                    image = image_item.get("local_path", "")

                    # CMS
                    from agents.cms_agent import CMSAgent
                    cms_r = await CMSAgent(dry_run=dry_run).execute(
                        article={
                            "title": title, "content_md": content,
                            "meta": {"meta_title": seo.get("meta_title", ""),
                                     "meta_description": seo.get("meta_description", "")},
                            "slug": slugify(title), "featured_image_url": image,
                        },
                        page_info={"category": "news",
                                   "tags": seo.get("keyword_result", {}).get("keywords", []),
                                   "slug": slugify(title)},
                        images={"featured_image_url": image, "featured_alt": title},
                    )

                    # 写入审计表
                    import aiomysql, os as _os
                    try:
                        pool = await aiomysql.create_pool(host=_os.environ["MYSQL_HOST"], port=int(_os.environ.get("MYSQL_PORT","3306")),
                            user=_os.environ["MYSQL_USER"], password=_os.environ["MYSQL_PASSWORD"], db="multi_agent_cms",
                            charset="utf8mb4", minsize=1, maxsize=1)
                        async with pool.acquire() as _c:
                            async with _c.cursor() as _cur:
                                kw = seo.get("keyword_result", {}).get("keywords", []) if isinstance(seo, dict) else []
                                meta_title = seo.get("meta_title", "") if isinstance(seo, dict) else ""
                                meta_desc = seo.get("meta_description", "") if isinstance(seo, dict) else ""
                                await _cur.execute(
                                    "UPDATE pipeline_audit SET image_prompt=%s, image_url=%s, image_local_path=%s, seo_keywords=%s, cms_status=%s, cms_article_id=%s, cms_article_url=%s WHERE article_id=%s",
                                    (image_prompt, image_url, image,
                                     json.dumps({"keywords": kw, "meta_title": meta_title, "meta_description": meta_desc}, ensure_ascii=False),
                                     cms_r.get("status"), cms_r.get("article_id"), cms_r.get("article_url"), item.get("article_id")))
                            await _c.commit()
                        pool.close(); await pool.wait_closed()
                    except Exception as audit_error:
                        print(f"[{CONSUMER}] 审计写入失败: {audit_error}")

                    print(f"[{CONSUMER}] {title[:40]} → CMS:{cms_r.get('status')}")
                except Exception as e:
                    print(f"[{CONSUMER}] {title[:40]} 失败: {e}")

                await ack_message(r, STREAM_PUBLISH, GROUP_PUBLISH, msg_id)
                processed_count += 1
                if args.once or (args.max_messages and processed_count >= args.max_messages):
                    print(f"[{CONSUMER}] 已处理 {processed_count} 条，退出")
                    await r.aclose()
                    return


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()) or 0)
