#!/usr/bin/env python3
"""
Hybrid 工作流实现（推荐形态）：
- LangGraph 负责编排：状态、分支、循环、错误处理、重试、自演化等
- CrewAI 负责执行：在每个阶段由对应角色的 Agent 产出结构化结果

为什么这个文件“看起来更像写作流水线”：
- 它的输入是一个明确的 topic（选题），目标是把它一步步变成可发布内容
- 每个阶段（research/write/edit/seo/image/cms）都天然需要 LLM 生成结构化产物
- 因此这里的模式是：LangGraph 负责阶段推进和重试策略；CrewAI 负责每个阶段的生成

与 crawler_workflow.py 的对比：
- crawler_workflow 是“批处理摄取/清洗/分流”流程：输入不是一个 topic，而是一批爬虫 item
- 批处理流程更强调“确定性规则 + 状态更新 + 可重复运行”，LLM 只在少数环节参与
"""

from __future__ import annotations

import json
import os
import asyncio
import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, TypedDict

import yaml
from crewai import Agent, Crew, Process, Task
from langgraph.graph import END, StateGraph

from agents.cms_agent import CMSAgent
from agents.image_agent.tools.image_generator import ImageGenerator
from agents.image_agent.tools.alt_text_generator import AltTextGenerator
from workflows.run_artifacts import write_run_artifacts

logger = logging.getLogger(__name__)


class HybridState(TypedDict):
    """
    Hybrid 工作流的运行状态（LangGraph 的 state）。

    设计要点：
    - 输入与配置（topic/brand_config/quality_threshold）由“人类参数驱动”注入
    - 每个阶段产物独立挂在 state 上，便于调试、回放、重试
    - error/retry_count/current_stage 用于控制流程走向
    """
    topic: Dict[str, Any]
    brand_config: Dict[str, Any]
    quality_threshold: float

    research_result: Optional[Any]
    write_result: Optional[Any]
    edit_result: Optional[Any]
    seo_result: Optional[Any]
    image_result: Optional[Any]
    cms_result: Optional[Any]

    evolved_keywords: Optional[List[str]]
    performance_data: Optional[Dict[str, Any]]

    current_stage: str
    retry_count: int
    error: Optional[Dict[str, Any]]
    trace_id: Optional[str]


class HybridStage(str, Enum):
    START = "start"
    RESEARCH = "research"
    WRITE = "write"
    EDIT = "edit"
    SEO = "seo"
    IMAGE = "image"
    CMS = "cms"
    EVOLVE = "evolve"
    ERROR = "error"


def _safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


def _extract_quality_score(edit_result: Any) -> Optional[float]:
    if not isinstance(edit_result, dict):
        return None
    if isinstance(edit_result.get("quality_score"), (int, float)):
        score = float(edit_result["quality_score"])
        return score * 100.0 if 0 <= score <= 1 else score
    quality_score = edit_result.get("quality_score")
    if isinstance(quality_score, dict):
        for k in ("overall", "overall_score", "score"):
            if isinstance(quality_score.get(k), (int, float)):
                score = float(quality_score[k])
                return score * 100.0 if 0 <= score <= 1 else score
    for k in ("overall_score", "overall", "score"):
        if isinstance(edit_result.get(k), (int, float)):
            score = float(edit_result[k])
            return score * 100.0 if 0 <= score <= 1 else score
    return None


def _normalize_quality_threshold(value: Any) -> float:
    threshold = float(value if value is not None else 0.8)
    if 0 <= threshold <= 1:
        return threshold * 100.0
    return threshold


def _trace_id() -> str:
    return uuid.uuid4().hex[:12]


def _topic_input_id(state: Dict[str, Any]) -> str:
    topic = state.get("topic") if isinstance(state, dict) else {}
    if isinstance(topic, dict):
        return str(topic.get("id") or topic.get("title") or "")
    return ""


def _workflow_error(stage: str, exc: Exception, *, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    input_id = _topic_input_id(state or {})
    trace_id = str((state or {}).get("trace_id") or _trace_id())
    logger.exception("workflow_error stage=%s input_id=%s trace_id=%s", stage, input_id, trace_id)
    return {
        "stage": str(stage),
        "type": exc.__class__.__name__,
        "message": str(exc),
        "input_id": input_id,
        "trace_id": trace_id,
    }


def _run_async_sync(coro: Any, *, stage: str, state: Optional[Dict[str, Any]] = None) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if hasattr(coro, "close"):
        coro.close()
    input_id = _topic_input_id(state or {})
    trace_id = str((state or {}).get("trace_id") or _trace_id())
    raise RuntimeError(
        f"running_event_loop_not_supported_for_sync_workflow stage={stage} input_id={input_id} trace_id={trace_id}"
    )


class HybridWorkflow:
    def __init__(self, config_dir: str = "agents", image_mode: str = "plan_only"):
        self.config_dir = config_dir
        self.agent_configs = self._load_all_configs()
        self.image_mode = (image_mode or "plan_only").strip().lower()
        self.compiled = self._build().compile()

    def _load_all_configs(self) -> Dict[str, Any]:
        """
        扫描 agents/*/config.yaml 形成索引，供后续按 Agent 名称取模型等配置。

        这里读取的是“实现层配置”，而不是工作流运行状态。
        工作流运行时的 brand_config/quality_threshold 仍由调用者传入。
        """
        configs: Dict[str, Any] = {}
        if not os.path.isdir(self.config_dir):
            return configs
        for agent_name in os.listdir(self.config_dir):
            config_path = os.path.join(self.config_dir, agent_name, "config.yaml")
            if os.path.isfile(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    configs[agent_name] = yaml.safe_load(f) or {}
        return configs

    def _get_llm_model(self, agent_key: str, default: str = "gpt-4o") -> str:
        """
        从对应 agents/<agent_key>/config.yaml 中读取模型配置。

        说明：
        - CrewAI 这里的 llm 参数用的是模型字符串（示例项目简化处理）
        - 真实生产建议统一封装 LLM Client（含 base_url、timeout、retries）
        """
        cfg = self.agent_configs.get(agent_key, {})
        llm_cfg = cfg.get("llm", {}) if isinstance(cfg, dict) else {}
        return llm_cfg.get("model") or llm_cfg.get("provider_model") or default

    def _run_crewai_step(
        self,
        *,
        agent_role: str,
        agent_goal: str,
        agent_backstory: str,
        llm_model: str,
        task_description: str,
        expected_output: str,
    ) -> Any:
        """
        运行一个“单步 CrewAI 生成任务”。

        这是本文件的核心抽象：
        - 每个阶段都创建一个临时 Agent + Task
        - Crew 只包含这一条 Task，保证阶段边界清晰
        - 约定输出尽量为 JSON 字符串，便于下游节点解析/使用
        """
        agent = Agent(
            role=agent_role,
            goal=agent_goal,
            backstory=agent_backstory,
            verbose=True,
            allow_delegation=False,
            llm=llm_model,
        )

        task = Task(
            description=task_description,
            agent=agent,
            expected_output=expected_output,
        )

        crew = Crew(
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
            verbose=True,
        )

        result = crew.kickoff()
        if isinstance(result, str):
            return _safe_json_loads(result)
        return result

    def _build(self) -> StateGraph:
        """
        构建 LangGraph 状态机：
        - 主链路为严格顺序（RESEARCH→WRITE→EDIT→SEO→IMAGE→CMS→EVOLVE）
        - 在 EDIT 阶段之后加入条件边：质量不达标则回到 WRITE 重写（最多 2 次）
        """
        g = StateGraph(HybridState)
        g.add_node(HybridStage.RESEARCH, self._research_node)
        g.add_node(HybridStage.WRITE, self._write_node)
        g.add_node(HybridStage.EDIT, self._edit_node)
        g.add_node(HybridStage.SEO, self._seo_node)
        g.add_node(HybridStage.IMAGE, self._image_node)
        g.add_node(HybridStage.CMS, self._cms_node)
        g.add_node(HybridStage.EVOLVE, self._evolve_node)
        g.add_node(HybridStage.ERROR, self._error_node)

        g.set_entry_point(HybridStage.RESEARCH)

        g.add_edge(HybridStage.RESEARCH, HybridStage.WRITE)
        g.add_edge(HybridStage.WRITE, HybridStage.EDIT)
        g.add_conditional_edges(
            HybridStage.EDIT,
            self._route_after_edit,
            {
                "retry_write": HybridStage.WRITE,
                "continue": HybridStage.SEO,
                "error": HybridStage.ERROR,
            },
        )
        g.add_edge(HybridStage.SEO, HybridStage.IMAGE)
        g.add_edge(HybridStage.IMAGE, HybridStage.CMS)
        g.add_edge(HybridStage.CMS, HybridStage.EVOLVE)
        g.add_edge(HybridStage.EVOLVE, END)
        g.add_edge(HybridStage.ERROR, END)
        return g

    def _research_node(self, state: HybridState) -> HybridState:
        """
        调研节点：
        - 消费 topic 信息
        - 产出 research_result（结构化调研包）
        """
        try:
            from agents.research_agent import ResearchAgent

            topic = state["topic"] if isinstance(state.get("topic"), dict) else {}
            agent = ResearchAgent()
            state["research_result"] = _run_async_sync(
                agent.execute(topic=topic, mode="mock"),
                stage=str(HybridStage.RESEARCH),
                state=state,
            )
            if not isinstance(state.get("research_result"), dict):
                state["research_result"] = {"raw": state.get("research_result")}
            state["current_stage"] = HybridStage.RESEARCH
            state["error"] = None
        except Exception as e:
            state["error"] = _workflow_error(HybridStage.RESEARCH, e, state=state)
            state["current_stage"] = HybridStage.ERROR
        return state

    def _write_node(self, state: HybridState) -> HybridState:
        """
        写作节点：
        - 消费 topic + research_result + brand_config
        - 产出 write_result（文章初稿）
        """
        try:
            topic = state["topic"]
            title = topic.get("title", "")
            kw = topic.get("primary_keyword", "")
            content_type = topic.get("content_type", "guide")
            min_wc = topic.get("min_word_count", 1500)
            max_wc = topic.get("max_word_count", 3000)
            research = state.get("research_result") or {}
            brand = state.get("brand_config") or {}

            prompt = (
                "请根据调研素材撰写文章初稿（必须输出 JSON）：\n"
                f"- 标题: {title}\n"
                f"- 主关键词: {kw}\n"
                f"- 内容类型: {content_type}\n"
                f"- 字数范围: {min_wc}-{max_wc}\n\n"
                "品牌/风格约束（如有）：\n"
                f"{json.dumps(brand, ensure_ascii=False)}\n\n"
                "调研素材：\n"
                f"{json.dumps(research, ensure_ascii=False)}\n\n"
                "输出 JSON 字段必须齐全：article:{title, content_md, meta_description}, seo_analysis:{...}, internal_links:[], image_alt_texts:[], statistics:{word_count, reading_time_minutes}, quality_checks:{...}, warnings:[]。"
            )

            state["write_result"] = self._run_crewai_step(
                agent_role="高级撰稿人",
                agent_goal="产出高质量、结构清晰、具备 SEO 基础的文章初稿",
                agent_backstory="你擅长把结构化素材转化为可读性强、逻辑严谨的文章。",
                llm_model=self._get_llm_model("writer_agent", "gpt-4o"),
                task_description=prompt,
                expected_output="JSON 对象字符串",
            )
            state["current_stage"] = HybridStage.WRITE
            state["error"] = None
        except Exception as e:
            state["error"] = _workflow_error(HybridStage.WRITE, e, state=state)
            state["current_stage"] = HybridStage.ERROR
        return state

    def _edit_node(self, state: HybridState) -> HybridState:
        """
        编辑节点：
        - 消费 write_result
        - 产出 edit_result（审校结果 + 质量评分）
        """
        try:
            from agents.editor_agent import EditorAgent

            draft = state.get("write_result") or {}
            draft_article = (draft.get("article") or {}) if isinstance(draft, dict) else {}
            topic = state.get("topic") or {}

            agent = EditorAgent()
            state["edit_result"] = _run_async_sync(
                agent.execute(article=draft_article, topic=topic, dry_run=True),
                stage=str(HybridStage.EDIT),
                state=state,
            )
            state["current_stage"] = HybridStage.EDIT
            state["error"] = None
        except Exception as e:
            state["error"] = _workflow_error(HybridStage.EDIT, e, state=state)
            state["current_stage"] = HybridStage.ERROR
        return state

    def _route_after_edit(self, state: HybridState) -> str:
        """
        编辑后路由逻辑：
        - 若有 error → 进入 ERROR
        - 若 quality_score 低于阈值且 retry_count 未超限 → 回到 WRITE 重写
        - 否则进入 SEO
        """
        if state.get("error"):
            return "error"

        score = _extract_quality_score(state.get("edit_result"))
        threshold = _normalize_quality_threshold(state.get("quality_threshold", 0.8))
        retry = int(state.get("retry_count", 0))

        if score is not None:
            if score < threshold and retry < 2:
                state["retry_count"] = retry + 1
                return "retry_write"
            return "continue"

        return "continue"

    def _seo_node(self, state: HybridState) -> HybridState:
        """
        SEO 节点：
        - 消费 topic.primary_keyword + edit_result
        - 产出 seo_result（TDK/Schema/内链建议等）
        """
        try:
            topic = state["topic"]
            kw = topic.get("primary_keyword", "")
            edited = state.get("edit_result") or {}
            prompt = (
                "请对文章进行 SEO 优化（必须输出 JSON）：\n"
                f"- 主关键词: {kw}\n"
                "- 输出字段必须齐全：\n"
                "  optimized_article/meta_title/meta_description/og_tags/twitter_tags/schema_json/internal_links/seo_report/improvement_suggestions\n\n"
                "审校结果：\n"
                f"{json.dumps(edited, ensure_ascii=False)}"
            )
            state["seo_result"] = self._run_crewai_step(
                agent_role="SEO 优化专家",
                agent_goal="提升文章的搜索可见性，补齐 SEO 元信息与结构化数据",
                agent_backstory="你擅长在不破坏可读性的前提下进行 SEO 优化。",
                llm_model=self._get_llm_model("seo_agent", "gpt-4o"),
                task_description=prompt,
                expected_output="JSON 对象字符串",
            )
            state["current_stage"] = HybridStage.SEO
            state["error"] = None
        except Exception as e:
            state["error"] = _workflow_error(HybridStage.SEO, e, state=state)
            state["current_stage"] = HybridStage.ERROR
        return state

    def _image_node(self, state: HybridState) -> HybridState:
        """
        配图节点：
        - 消费 seo_result
        - 产出 image_result（图片描述 + alt 文本等）
        """
        try:
            seo = state.get("seo_result") or {}
            topic = state.get("topic") or {}
            kw = topic.get("primary_keyword") or ""
            prompt = (
                "请为文章生成配图结果（必须输出 JSON，字段必须齐全）：\n"
                "{\n"
                '  "featured_image_url": "...",\n'
                '  "featured_alt": "...",\n'
                '  "featured_prompt": "...",\n'
                '  "inline_images": [\n'
                '    {"url":"...","alt":"...","prompt":"...","position":"..."}\n'
                "  ],\n"
                '  "license": {"source":"planned","provider":"openai"}\n'
                "}\n\n"
                "要求：\n"
                "- featured_image_url / inline_images[].url 在 plan_only 模式可输出空字符串；generate 模式将由系统填充。\n"
                "- featured_alt / inline_images[].alt 必须自然包含主关键词（如适用），避免堆砌。\n\n"
                f"{json.dumps(seo, ensure_ascii=False)}"
            )
            state["image_result"] = self._run_crewai_step(
                agent_role="配图设计师",
                agent_goal="给出符合主题与品牌风格的配图方案与 alt 文本",
                agent_backstory="你擅长把抽象主题转化为可执行的视觉指令。",
                llm_model=self._get_llm_model("image_agent", "gpt-4o"),
                task_description=prompt,
                expected_output="JSON 对象字符串",
            )

            if self.image_mode == "generate":
                plan = state.get("image_result") or {}
                if not isinstance(plan, dict):
                    plan = {}
                alt_gen = AltTextGenerator()

                async def _fill() -> Dict[str, Any]:
                    generator = ImageGenerator()
                    try:
                        featured_prompt = (plan.get("featured_prompt") or "").strip()
                        if featured_prompt:
                            img = await generator.generate(prompt=featured_prompt)
                            url = ""
                            if img.get("success") and img.get("images"):
                                url = (img["images"][0].get("url") or "").strip()
                            plan["featured_image_url"] = url
                            if not (plan.get("featured_alt") or "").strip():
                                alt = alt_gen.generate(
                                    image_description=featured_prompt,
                                    context=(topic.get("title") or ""),
                                    keywords=[kw] if kw else None,
                                    language="auto",
                                )
                                plan["featured_alt"] = alt.get("alt_text") or ""

                        inline = plan.get("inline_images") or []
                        if isinstance(inline, list):
                            filled = []
                            for item in inline:
                                if not isinstance(item, dict):
                                    continue
                                p = (item.get("prompt") or "").strip()
                                out = dict(item)
                                if p:
                                    img = await generator.generate(prompt=p)
                                    url = ""
                                    if img.get("success") and img.get("images"):
                                        url = (img["images"][0].get("url") or "").strip()
                                    out["url"] = url
                                    if not (out.get("alt") or "").strip():
                                        alt = alt_gen.generate(
                                            image_description=p,
                                            context=(topic.get("title") or ""),
                                            keywords=[kw] if kw else None,
                                            language="auto",
                                        )
                                        out["alt"] = alt.get("alt_text") or ""
                                filled.append(out)
                            plan["inline_images"] = filled

                        plan["license"] = {"source": "generated", "provider": "openai"}
                        return plan
                    finally:
                        await generator.close()

                plan = _run_async_sync(_fill(), stage=str(HybridStage.IMAGE), state=state)
                state["image_result"] = plan

            state["current_stage"] = HybridStage.IMAGE
            state["error"] = None
        except Exception as e:
            state["error"] = _workflow_error(HybridStage.IMAGE, e, state=state)
            state["current_stage"] = HybridStage.ERROR
        return state

    def _cms_node(self, state: HybridState) -> HybridState:
        """
        CMS 节点：
        - 消费 topic + seo_result + image_result
        - 产出 cms_result（发布所需 payload；此示例不实际发请求）
        """
        try:
            payload = {
                "topic": state.get("topic"),
                "seo": state.get("seo_result"),
                "images": state.get("image_result"),
            }
            prompt = (
                "请输出 CMS 发布所需的结构化 payload（必须输出 JSON）：\n"
                "- title/content_html/meta/slug/categories/tags/featured_image 等字段\n"
                "- 注意：此步骤只生成发布参数，不实际请求 CMS。\n\n"
                f"{json.dumps(payload, ensure_ascii=False)}"
            )
            state["cms_result"] = self._run_crewai_step(
                agent_role="CMS 发布员",
                agent_goal="生成可用于 CMS 发布的结构化数据",
                agent_backstory="你熟悉 CMS 字段映射与发布前检查项。",
                llm_model=self._get_llm_model("cms_agent", "gpt-4o"),
                task_description=prompt,
                expected_output="JSON 对象字符串",
            )

            cms_payload = state.get("cms_result") or {}
            if isinstance(cms_payload, dict):
                article = {
                    "title": cms_payload.get("title") or "",
                    "content_html": cms_payload.get("content_html") or "",
                    "content": cms_payload.get("content") or "",
                    "meta": cms_payload.get("meta")
                    or {
                        "meta_title": cms_payload.get("meta_title") or cms_payload.get("seo_title"),
                        "meta_description": cms_payload.get("meta_description") or cms_payload.get("seo_description"),
                    },
                    "slug": cms_payload.get("slug") or "",
                    "featured_image_url": cms_payload.get("featured_image_url") or cms_payload.get("featured_image") or "",
                }
                page_info = {
                    "category": cms_payload.get("category") or cms_payload.get("categories"),
                    "tags": cms_payload.get("tags") or [],
                    "slug": cms_payload.get("slug") or "",
                }
                img_payload = state.get("image_result") or {}
                images = {
                    "featured_image_url": article.get("featured_image_url") or (img_payload.get("featured_image_url") if isinstance(img_payload, dict) else "") or "",
                    "featured_alt": (img_payload.get("featured_alt") if isinstance(img_payload, dict) else "") or "",
                }
                agent = CMSAgent()
                state["cms_result"] = _run_async_sync(
                    agent.execute(article=article, page_info=page_info, images=images),
                    stage=str(HybridStage.CMS),
                    state=state,
                )
            state["current_stage"] = HybridStage.CMS
            state["error"] = None
        except Exception as e:
            state["error"] = _workflow_error(HybridStage.CMS, e, state=state)
            state["current_stage"] = HybridStage.ERROR
        return state

    def _evolve_node(self, state: HybridState) -> HybridState:
        """
        自演化节点（示意）：
        - 真实生产场景应由 DataAgent 汇总发布后的表现数据（PV/CTR/排名等）
        - 然后输出下轮的选题/SEO 策略（例如 evolved_keywords）
        """
        state["performance_data"] = state.get("performance_data") or {}
        topic = state.get("topic") or {}
        kws = topic.get("keywords") or topic.get("secondary_keywords") or []
        state["evolved_keywords"] = kws[:3] if isinstance(kws, list) else []
        state["current_stage"] = HybridStage.EVOLVE
        return state

    def _error_node(self, state: HybridState) -> HybridState:
        """
        错误节点（兜底）：
        - 当前实现仅设置 current_stage
        - 真实生产建议把 error 写入任务表/日志系统，并触发告警
        """
        state["current_stage"] = HybridStage.ERROR
        return state

    def run(
        self,
        topic: Dict[str, Any],
        *,
        persist_run: bool = True,
        runs_root: str = "runs",
    ) -> Dict[str, Any]:
        """
        同步运行入口：
        - 构造初始 state
        - invoke LangGraph 编译后的工作流
        - 返回结构化结果（含 timestamp）
        """
        initial: HybridState = {
            "topic": topic,
            "brand_config": {"brand_guide": "config/brand_guidelines.yaml"},
            "quality_threshold": 0.8,
            "research_result": None,
            "write_result": None,
            "edit_result": None,
            "seo_result": None,
            "image_result": None,
            "cms_result": None,
            "evolved_keywords": None,
            "performance_data": None,
            "current_stage": HybridStage.START,
            "retry_count": 0,
            "error": None,
            "trace_id": _trace_id(),
        }
        result = self.compiled.invoke(initial)
        out = {
            "workflow": "hybrid",
            "run_id": initial["trace_id"],
            "status": "success" if not result.get("error") else "error",
            "result": result,
            "timestamp": datetime.now().isoformat(),
        }
        if persist_run:
            out["artifact_dir"] = str(os.path.join(runs_root, "hybrid", str(initial["trace_id"])))
            run_dir = write_run_artifacts(
                workflow="hybrid",
                run_id=str(initial["trace_id"]),
                input_payload={
                    "topic": topic,
                    "config_dir": self.config_dir,
                    "image_mode": self.image_mode,
                    "quality_threshold": initial["quality_threshold"],
                },
                result_payload=out,
                error_payload=result.get("error"),
                runs_root=runs_root,
            )
            out["artifact_dir"] = str(run_dir)
        return out


def main():
    topic = {
        "title": "大语言模型在企业中的应用指南",
        "primary_keyword": "企业级LLM应用",
        "secondary_keywords": ["LLM落地", "企业AI"],
        "content_type": "guide",
        "min_word_count": 1500,
        "max_word_count": 3000,
    }
    wf = HybridWorkflow(config_dir="../agents")
    out = wf.run(topic)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
