"""Generate WriterAgent articles from research candidates and persist outputs."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional

from agents.topic_agent.tools.article_score_writer import validate_identifier


DEFAULT_RESEARCH_DATABASE = "research_article_data"
DEFAULT_CANDIDATE_TABLE = "research_article_candidates"
DEFAULT_OUTPUT_TABLE = "writer_article_outputs"
DEFAULT_WRITER_MODEL = "deepseek-chat"
DEFAULT_WRITER_BASE_URL = "https://api.deepseek.com"


def _json_dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _clean_db_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _extract_json(text: Any) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if "```" in raw:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start : end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
        return json.loads(cleaned)


def _json_loads_maybe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _text_char_count(text: Any) -> int:
    return len(str(text or ""))


def _word_policy_from_candidate(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    brief = _json_loads_maybe(candidate.get("research_brief"))
    if not isinstance(brief, Mapping):
        return {"min": 900, "max": 1200, "target": 1100, "is_notice": False}
    instruction = brief.get("word_count_instruction")
    if not isinstance(instruction, Mapping):
        return {"min": 900, "max": 1200, "target": 1100, "is_notice": False}
    try:
        min_words = int(instruction.get("standard_min_words") or 900)
    except Exception:
        min_words = 900
    try:
        max_words = int(instruction.get("standard_max_words") or 1200)
    except Exception:
        max_words = 1200
    try:
        target = int(instruction.get("target_word_count") or int((min_words + max_words) / 2))
    except Exception:
        target = int((min_words + max_words) / 2)
    return {
        "min": min_words,
        "max": max_words,
        "target": target,
        "is_notice": bool(instruction.get("is_notice")),
    }


def _word_policy_warning(candidate: Mapping[str, Any], generation: Mapping[str, Any]) -> Optional[str]:
    policy = _word_policy_from_candidate(candidate)
    if policy.get("is_notice"):
        return None
    article = generation.get("article") if isinstance(generation.get("article"), Mapping) else {}
    chars = _text_char_count(article.get("content_md"))
    if chars < int(policy["min"]):
        return f"content_too_short:{chars}<{policy['min']}"
    if chars > int(policy["max"]):
        return f"content_too_long:{chars}>{policy['max']}"
    return None


def _normalize_article_output(payload: Mapping[str, Any]) -> Dict[str, Any]:
    payload = payload if isinstance(payload, Mapping) else {}
    article = payload.get("article") if isinstance(payload.get("article"), Mapping) else {}
    statistics = payload.get("statistics") if isinstance(payload.get("statistics"), Mapping) else {}
    return {
        "article": {
            "title": str(article.get("title") or ""),
            "meta_description": str(article.get("meta_description") or ""),
            "content_md": str(article.get("content_md") or article.get("content") or ""),
            "title_options": article.get("title_options") if isinstance(article.get("title_options"), list) else [],
        },
        "seo_analysis": payload.get("seo_analysis") if isinstance(payload.get("seo_analysis"), Mapping) else {},
        "internal_links": payload.get("internal_links") if isinstance(payload.get("internal_links"), list) else [],
        "image_alt_texts": payload.get("image_alt_texts") if isinstance(payload.get("image_alt_texts"), list) else [],
        "statistics": {
            "word_count": int(statistics.get("word_count") or 0),
            "reading_time_minutes": int(statistics.get("reading_time_minutes") or 0),
        },
        "quality_checks": payload.get("quality_checks") if isinstance(payload.get("quality_checks"), Mapping) else {},
        "warnings": payload.get("warnings") if isinstance(payload.get("warnings"), list) else [],
    }


def build_writer_generation_prompt(candidate: Mapping[str, Any]) -> str:
    """Wrap the stored ResearchAgent prompt with source metadata."""

    prompt = str(candidate.get("writer_prompt") or "").strip()
    if not prompt:
        raise ValueError("missing_writer_prompt")

    metadata = {
        "candidate_id": candidate.get("id"),
        "source_article_id": candidate.get("source_article_id"),
        "original_url": candidate.get("original_url"),
        "source_title": candidate.get("title"),
        "article_score": candidate.get("article_score"),
    }
    policy = _word_policy_from_candidate(candidate)
    if policy.get("is_notice"):
        policy_line = (
            f"这篇是通知/公告类，content_md 建议 {policy['min']}-{policy['max']} 字，"
            f"约 {policy['target']} 字即可。"
        )
    else:
        policy_line = (
            f"硬性字数验收：这篇不是通知，content_md 必须写到 {policy['min']}-{policy['max']} 个中文字符左右，"
            f"建议约 {policy['target']} 字；少于 {policy['min']} 字或多于 {policy['max']} 字都视为不合格。"
            "请按最终 Markdown 正文的实际字符长度自检，不要只按段落数量估算。"
        )
    return "\n".join(
        [
            "以下是本次写作任务的数据库元信息，请作为事实约束使用：",
            json.dumps(metadata, ensure_ascii=False, indent=2),
            "",
            policy_line,
            "",
            "以下是 ResearchAgent 已生成的 WriterAgent 提示词，请严格按其中要求输出纯 JSON：",
            prompt,
        ]
    ).strip()


def build_writer_output_payload(
    candidate: Mapping[str, Any],
    generation: Optional[Mapping[str, Any]] = None,
    *,
    writer_model: Optional[str] = None,
    error_message: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert candidate + generation result into DB output payload."""

    candidate = candidate if isinstance(candidate, Mapping) else {}
    generation = generation if isinstance(generation, Mapping) else None
    article = generation.get("article") if isinstance(generation, Mapping) and isinstance(generation.get("article"), Mapping) else {}
    status = "generated" if generation and not error_message else "failed"
    return {
        "candidate_id": candidate.get("id") or candidate.get("candidate_id"),
        "source_article_id": candidate.get("source_article_id"),
        "original_url": candidate.get("original_url"),
        "source_title": candidate.get("title") or candidate.get("source_title"),
        "article_score": candidate.get("article_score"),
        "writer_prompt": candidate.get("writer_prompt"),
        "writer_model": writer_model,
        "generation_status": status,
        "generated_title": article.get("title") if article else None,
        "generated_meta_description": article.get("meta_description") if article else None,
        "generated_content_md": article.get("content_md") if article else None,
        "generated_article_json": dict(generation) if generation else None,
        "quality_checks": generation.get("quality_checks") if isinstance(generation, Mapping) else None,
        "warnings": generation.get("warnings") if isinstance(generation, Mapping) else None,
        "error_message": error_message,
    }


@dataclass
class WriterLLMConfig:
    api_key: str
    model: str = DEFAULT_WRITER_MODEL
    base_url: str = DEFAULT_WRITER_BASE_URL
    temperature: float = 0.7
    timeout: int = 120

    @classmethod
    def from_env(cls) -> "WriterLLMConfig":
        api_key = (
            os.getenv("WRITER_AGENT_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("ARTICLE_SCORING_API_KEY")
            or ""
        ).strip()
        model = (os.getenv("WRITER_AGENT_MODEL") or os.getenv("ARTICLE_SCORING_MODEL") or DEFAULT_WRITER_MODEL).strip()
        base_url = (
            os.getenv("WRITER_AGENT_BASE_URL")
            or os.getenv("ARTICLE_SCORING_BASE_URL")
            or DEFAULT_WRITER_BASE_URL
        ).strip()
        temperature_raw = os.getenv("WRITER_AGENT_TEMPERATURE", "0.7")
        try:
            temperature = float(temperature_raw)
        except ValueError:
            temperature = 0.7
        return cls(api_key=api_key, model=model, base_url=base_url, temperature=temperature)

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


class OpenAICompatibleWriterClient:
    """Small OpenAI-compatible chat client for WriterAgent generation."""

    def __init__(self, config: Optional[WriterLLMConfig] = None):
        self.config = config or WriterLLMConfig.from_env()

    async def generate(self, prompt: str) -> Dict[str, Any]:
        if not self.config.is_configured:
            raise RuntimeError("writer_agent_api_key_missing")

        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout,
        )
        resp = await client.chat.completions.create(
            model=self.config.model,
            temperature=self.config.temperature,
            messages=[
                {
                    "role": "system",
                    "content": "你是 WriterAgent。必须只输出一个 JSON 对象，不要输出代码块、解释文字或前后缀。",
                },
                {"role": "user", "content": prompt},
            ],
        )
        content = resp.choices[0].message.content if resp.choices else ""
        return _normalize_article_output(_extract_json(content))


class WriterArticleDB:
    """Read research candidates and write generated articles."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = dict(config or {})
        self.host = self.config.get("host", "localhost")
        self.port = int(self.config.get("port", 3306))
        self.database = validate_identifier(self.config.get("database", DEFAULT_RESEARCH_DATABASE))
        self.user = self.config.get("user", "root")
        self.password = self.config.get("password", "")
        self.candidate_table = validate_identifier(self.config.get("candidate_table", DEFAULT_CANDIDATE_TABLE))
        self.output_table = validate_identifier(self.config.get("output_table", DEFAULT_OUTPUT_TABLE))
        self._conn = None

    async def _get_conn(self):
        if self._conn is None:
            import aiomysql

            self._conn = await aiomysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                db=self.database,
                charset="utf8mb4",
                autocommit=False,
            )
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def fetch_generated_candidates(self, limit: int = 10, only_missing_outputs: bool = True) -> List[Dict[str, Any]]:
        import aiomysql

        conn = await self._get_conn()
        limit = max(1, int(limit))
        missing_clause = ""
        if only_missing_outputs:
            missing_clause = f"""
                AND NOT EXISTS (
                    SELECT 1
                    FROM `{self.output_table}` o
                    WHERE o.candidate_id = c.id
                      AND o.generation_status = 'generated'
                )
            """
        query = f"""
            SELECT c.*
            FROM `{self.candidate_table}` c
            WHERE c.research_status = 'generated'
              AND c.writer_prompt IS NOT NULL
              AND c.writer_prompt <> ''
              {missing_clause}
            ORDER BY c.article_score DESC, c.id ASC
            LIMIT %s
        """
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(query, (limit,))
            rows = await cursor.fetchall()
        return [{k: _clean_db_value(v) for k, v in row.items()} for row in rows]

    async def write_outputs(self, outputs: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        rows = list(outputs)
        if not rows:
            return {"success": True, "inserted_or_updated": 0}

        conn = await self._get_conn()
        query = f"""
            INSERT INTO `{self.output_table}` (
                candidate_id,
                source_article_id,
                original_url,
                source_title,
                article_score,
                writer_prompt,
                writer_model,
                generation_status,
                generated_title,
                generated_meta_description,
                generated_content_md,
                generated_article_json,
                quality_checks,
                warnings,
                error_message,
                generated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                CAST(%s AS JSON), CAST(%s AS JSON), CAST(%s AS JSON), %s,
                CASE WHEN %s = 'generated' THEN NOW() ELSE NULL END
            )
            ON DUPLICATE KEY UPDATE
                source_article_id = VALUES(source_article_id),
                original_url = VALUES(original_url),
                source_title = VALUES(source_title),
                article_score = VALUES(article_score),
                writer_prompt = VALUES(writer_prompt),
                writer_model = VALUES(writer_model),
                generation_status = VALUES(generation_status),
                generated_title = VALUES(generated_title),
                generated_meta_description = VALUES(generated_meta_description),
                generated_content_md = VALUES(generated_content_md),
                generated_article_json = VALUES(generated_article_json),
                quality_checks = VALUES(quality_checks),
                warnings = VALUES(warnings),
                error_message = VALUES(error_message),
                generated_at = VALUES(generated_at)
        """
        params = [
            (
                row.get("candidate_id"),
                row.get("source_article_id"),
                row.get("original_url"),
                row.get("source_title"),
                row.get("article_score"),
                row.get("writer_prompt"),
                row.get("writer_model"),
                row.get("generation_status"),
                row.get("generated_title"),
                row.get("generated_meta_description"),
                row.get("generated_content_md"),
                _json_dumps(row.get("generated_article_json")),
                _json_dumps(row.get("quality_checks")),
                _json_dumps(row.get("warnings")),
                row.get("error_message"),
                row.get("generation_status"),
            )
            for row in rows
        ]
        async with conn.cursor() as cursor:
            await cursor.executemany(query, params)
            await conn.commit()
            return {"success": True, "inserted_or_updated": cursor.rowcount}


async def generate_articles_from_research_db(
    db_config: Optional[Dict[str, Any]] = None,
    llm_config: Optional[WriterLLMConfig] = None,
    *,
    limit: int = 10,
    concurrency: int = 2,
    only_missing_outputs: bool = True,
) -> Dict[str, Any]:
    """Generate articles for research candidates and persist them."""

    db = WriterArticleDB(db_config)
    client = OpenAICompatibleWriterClient(llm_config)
    model = client.config.model
    try:
        candidates = await db.fetch_generated_candidates(limit=limit, only_missing_outputs=only_missing_outputs)
        sem = asyncio.Semaphore(max(1, int(concurrency)))

        async def one(candidate: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                try:
                    prompt = build_writer_generation_prompt(candidate)
                    generation = await client.generate(prompt)
                    warning = _word_policy_warning(candidate, generation)
                    retry_count = 0
                    while warning and retry_count < 3:
                        retry_count += 1
                        policy = _word_policy_from_candidate(candidate)
                        if warning.startswith("content_too_long"):
                            rewrite_detail = (
                                "这次重点是压缩。请把 content_md 压到 1000-1100 字左右，"
                                "删除重复背景、口号式总结和次要铺垫，只保留核心事实、影响和读者需要知道的下一步。"
                            )
                        else:
                            rewrite_detail = (
                                "这次重点是扩写。不要只写短讯；至少写 8-10 个自然段。"
                                "在不编造事实的前提下，用原文已有事实做背景解释、影响分析、读者关心的问题和后续关注点。"
                                "如果素材很短，也要围绕“为什么重要、影响谁、下一步看什么、读者需要注意什么”展开。"
                                "请把正文写到 950-1100 个中文字符左右，输出前按 content_md 的实际字符长度自检。"
                            )
                        retry_prompt = "\n\n".join(
                            [
                                prompt,
                                "上一版未通过字数验收。",
                                f"失败原因：{warning}。",
                                (
                                    f"请重新输出完整 JSON。content_md 必须控制在 "
                                    f"{policy['min']}-{policy['max']} 字，建议约 {policy['target']} 字。"
                                    f"{rewrite_detail}"
                                ),
                            ]
                        )
                        generation = await client.generate(retry_prompt)
                        warning = _word_policy_warning(candidate, generation)
                    if warning:
                        raise ValueError(warning)
                    return build_writer_output_payload(candidate, generation, writer_model=model)
                except Exception as exc:
                    return build_writer_output_payload(candidate, None, writer_model=model, error_message=str(exc))

        outputs = await asyncio.gather(*(one(candidate) for candidate in candidates))
        write_result = await db.write_outputs(outputs)
        generated = sum(1 for row in outputs if row.get("generation_status") == "generated")
        failed = len(outputs) - generated
        return {
            "success": failed == 0,
            "read": len(candidates),
            "generated": generated,
            "failed": failed,
            "write_result": write_result,
            "failures": [
                {
                    "candidate_id": row.get("candidate_id"),
                    "source_title": row.get("source_title"),
                    "error_message": row.get("error_message"),
                }
                for row in outputs
                if row.get("generation_status") == "failed"
            ],
        }
    finally:
        await db.close()
