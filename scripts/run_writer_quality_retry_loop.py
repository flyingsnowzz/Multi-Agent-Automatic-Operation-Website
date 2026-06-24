#!/usr/bin/env python3
"""Regenerate WriterAgent outputs until QualityAgent score reaches target."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.quality_agent.tools.article_quality_scorer import (  # noqa: E402
    ArticleQualityDB,
    OpenAICompatibleQualityClient,
    build_quality_output_payload,
)
from agents.writer_agent.tools.article_generation_writer import (  # noqa: E402
    OpenAICompatibleWriterClient,
    WriterArticleDB,
    build_writer_generation_prompt,
    build_writer_output_payload,
    _word_policy_from_candidate,
    _word_policy_warning,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retry WriterAgent until quality target")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="research_article_data")
    parser.add_argument("--candidate-table", default="research_article_candidates")
    parser.add_argument("--writer-output-table", default="writer_article_outputs")
    parser.add_argument("--quality-table", default="article_quality_scores")
    parser.add_argument("--target-quality", type=float, default=85.0)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--source-kind",
        choices=["writer"],
        default="writer",
        help="Quality source_kind to retry",
    )
    return parser.parse_args()


def _db_config(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "host": args.host,
        "port": args.port,
        "user": args.user,
        "password": args.password,
        "database": args.database,
        "candidate_table": args.candidate_table,
        "output_table": args.writer_output_table,
        "writer_output_table": args.writer_output_table,
        "quality_table": args.quality_table,
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


async def _fetch_retry_candidate_ids(args: argparse.Namespace) -> List[int]:
    import aiomysql

    conn = await aiomysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        db=args.database,
        charset="utf8mb4",
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT candidate_id
                FROM `{args.quality_table}`
                WHERE source_kind = %s
                  AND quality_status = 'scored'
                  AND quality_score < %s
                ORDER BY quality_score ASC, candidate_id ASC
                LIMIT %s
                """,
                (args.source_kind, float(args.target_quality), max(1, int(args.limit))),
            )
            rows = await cur.fetchall()
            return [int(row[0]) for row in rows if row and row[0] is not None]
    finally:
        conn.close()


async def _fetch_candidate(writer_db: WriterArticleDB, candidate_id: int) -> Optional[Dict[str, Any]]:
    import aiomysql

    conn = await writer_db._get_conn()
    async with conn.cursor(aiomysql.DictCursor) as cur:
        await cur.execute(
            f"SELECT * FROM `{writer_db.candidate_table}` WHERE id = %s LIMIT 1",
            (candidate_id,),
        )
        row = await cur.fetchone()
    return row


async def _fetch_quality_feedback(quality_db: ArticleQualityDB, candidate_id: int) -> Dict[str, Any]:
    import aiomysql

    conn = await quality_db._get_conn()
    async with conn.cursor(aiomysql.DictCursor) as cur:
        await cur.execute(
            f"""
            SELECT quality_score, rewrite_feedback_prompt, quality_payload
            FROM `{quality_db.quality_table}`
            WHERE source_kind = 'writer'
              AND candidate_id = %s
            LIMIT 1
            """,
            (candidate_id,),
        )
        row = await cur.fetchone()
    return dict(row or {})


async def _generate_with_feedback(
    candidate: Mapping[str, Any],
    feedback: Mapping[str, Any],
    writer_client: OpenAICompatibleWriterClient,
) -> Dict[str, Any]:
    prompt = build_writer_generation_prompt(candidate)
    feedback_text = str(feedback.get("rewrite_feedback_prompt") or "").strip()
    if feedback_text:
        prompt = "\n\n".join(
            [
                prompt,
                "## 上一轮 QualityAgent 扣分反馈",
                feedback_text,
                "请针对这些扣分点重写完整 JSON，不要只局部修补。尤其要降低普通人粗看时的 AI 感。",
            ]
        )

    generation = await writer_client.generate(prompt)
    warning = _word_policy_warning(candidate, generation)
    retry_count = 0
    while warning and retry_count < 3:
        retry_count += 1
        policy = _word_policy_from_candidate(candidate)
        prompt = "\n\n".join(
            [
                prompt,
                "上一版未通过字数验收。",
                f"失败原因：{warning}。",
                (
                    f"请重新输出完整 JSON。content_md 必须控制在 "
                    f"{policy['min']}-{policy['max']} 字，建议约 {policy['target']} 字。"
                ),
            ]
        )
        generation = await writer_client.generate(prompt)
        warning = _word_policy_warning(candidate, generation)
    if warning:
        raise ValueError(warning)
    return generation


def _quality_article_from_writer_payload(candidate: Mapping[str, Any], writer_payload: Mapping[str, Any]) -> Dict[str, Any]:
    article = writer_payload.get("generated_article_json")
    generated = article.get("article") if isinstance(article, Mapping) and isinstance(article.get("article"), Mapping) else {}
    return {
        "candidate_id": writer_payload.get("candidate_id"),
        "source_article_id": writer_payload.get("source_article_id"),
        "writer_output_id": None,
        "original_url": writer_payload.get("original_url"),
        "article_score": writer_payload.get("article_score"),
        "source_title": candidate.get("title"),
        "title": writer_payload.get("generated_title") or generated.get("title"),
        "content": writer_payload.get("generated_content_md") or generated.get("content_md"),
        "if_ai_generated": True,
    }


async def _score_writer_payload(
    candidate: Mapping[str, Any],
    writer_payload: Mapping[str, Any],
    quality_client: OpenAICompatibleQualityClient,
    quality_db: ArticleQualityDB,
    attempt: int,
) -> Dict[str, Any]:
    article = _quality_article_from_writer_payload(candidate, writer_payload)
    quality = await quality_client.score(article)
    quality["rewrite_attempt"] = attempt
    quality_payload = build_quality_output_payload(
        article,
        quality,
        source_kind="writer",
        model=quality_client.config.model,
    )
    await quality_db.write_quality_scores([quality_payload])
    return quality_payload


async def main() -> None:
    args = parse_args()
    db_config = _db_config(args)
    candidate_ids = await _fetch_retry_candidate_ids(args)
    writer_db = WriterArticleDB(db_config)
    quality_db = ArticleQualityDB(db_config)
    writer_client = OpenAICompatibleWriterClient()
    quality_client = OpenAICompatibleQualityClient()
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))
    summary = {
        "candidates": len(candidate_ids),
        "passed": 0,
        "not_passed": 0,
        "failed": 0,
        "attempts": 0,
        "items": [],
    }

    async def handle(candidate_id: int) -> Dict[str, Any]:
        async with sem:
            item = {
                "candidate_id": candidate_id,
                "attempts": 0,
                "initial_quality": None,
                "final_quality": None,
                "passed": False,
                "error": None,
            }
            try:
                candidate = await _fetch_candidate(writer_db, candidate_id)
                if not candidate:
                    raise ValueError("candidate_not_found")
                feedback = await _fetch_quality_feedback(quality_db, candidate_id)
                item["initial_quality"] = feedback.get("quality_score")
                final_quality = float(feedback.get("quality_score") or 0)
                last_error = None
                for attempt in range(1, max(1, int(args.max_attempts)) + 1):
                    if final_quality >= float(args.target_quality):
                        break
                    item["attempts"] = attempt
                    try:
                        generation = await _generate_with_feedback(candidate, feedback, writer_client)
                        writer_payload = build_writer_output_payload(
                            candidate,
                            generation,
                            writer_model=writer_client.config.model,
                        )
                        await writer_db.write_outputs([writer_payload])
                        quality_payload = await _score_writer_payload(
                            candidate,
                            writer_payload,
                            quality_client,
                            quality_db,
                            attempt,
                        )
                        final_quality = float(quality_payload.get("quality_score") or 0)
                        feedback = await _fetch_quality_feedback(quality_db, candidate_id)
                        last_error = None
                    except Exception as exc:
                        last_error = str(exc)
                        feedback_text = str(feedback.get("rewrite_feedback_prompt") or "").strip()
                        feedback = {
                            **dict(feedback),
                            "rewrite_feedback_prompt": "\n".join(
                                part
                                for part in [
                                    feedback_text,
                                    f"上一轮生成失败：{last_error}。请优先满足字数和完整正文要求。",
                                ]
                                if part
                            ),
                        }
                item["final_quality"] = round(final_quality, 2)
                item["passed"] = final_quality >= float(args.target_quality)
                if not item["passed"] and last_error:
                    item["error"] = last_error
            except Exception as exc:
                item["error"] = str(exc)
            return item

    try:
        items = await asyncio.gather(*(handle(candidate_id) for candidate_id in candidate_ids))
        summary["items"] = items
        summary["passed"] = sum(1 for item in items if item.get("passed"))
        summary["failed"] = sum(1 for item in items if item.get("error"))
        summary["not_passed"] = len(items) - summary["passed"] - summary["failed"]
        summary["attempts"] = sum(int(item.get("attempts") or 0) for item in items)
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
    finally:
        await writer_db.close()
        await quality_db.close()


if __name__ == "__main__":
    asyncio.run(main())
