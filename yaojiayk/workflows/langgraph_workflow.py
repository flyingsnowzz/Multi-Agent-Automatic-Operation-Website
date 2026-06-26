#!/usr/bin/env python3
"""
LangGraph工作流实现 - 多Agent内容生产流水线
版本: v1.0
创建时间: 2026-05-13

LangGraph优势:
1. 原生支持自演化反馈循环
2. 强大的状态管理
3. 复杂条件分支
4. 错误处理和重试

本文件在项目中的角色：
- “编排层”的参考实现：用状态机/有向图组织多阶段内容生产流程
- 每个节点负责一件事：读 prompt 模板 → 调用 LLM → 解析结构化 JSON → 写回 state

重要约定（为了让代码能跑通）：
- agents/*/prompt.md 模板中应输出 JSON 字符串（便于 json.loads 解析）
- 真实生产场景建议：使用 Pydantic/JSON Schema 做输出校验；并把中间产物落库，便于重试和追踪
"""

import os
import json
import yaml
import asyncio
import logging
import uuid
from typing import Dict, List, Any, Optional, TypedDict, Annotated
from enum import Enum
from datetime import datetime

# LangGraph相关导入
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI

# 自定义工具导入
from agents.topic_agent.tools.keyword_research import KeywordResearchTool
from agents.topic_agent.tools.trend_detection import TrendDetectionTool
from agents.topic_agent.tools.serp_analysis import SERPAnalysisTool
from agents.cms_agent import CMSAgent
from agents.image_agent.tools.image_generator import ImageGenerator
from agents.image_agent.tools.alt_text_generator import AltTextGenerator

logger = logging.getLogger(__name__)


class PipelineState(TypedDict):
    """流水线状态定义"""

    # 输入
    topic: Dict[str, Any]
    brand_config: Dict[str, Any]  # 品牌风格参数（人类配置）
    quality_threshold: float     # 质量阈值（人类配置）

    # 各阶段输出
    research_result: Optional[Dict]
    write_result: Optional[Dict]
    edit_result: Optional[Dict]
    seo_result: Optional[Dict]
    image_result: Optional[Dict]
    cms_result: Optional[Dict]

    # 自演化反馈
    evolved_keywords: Optional[List[str]]  # 演化后的关键词
    performance_data: Optional[Dict]        # 性能数据

    # 状态追踪
    current_stage: str
    error: Optional[Dict[str, Any]]
    retry_count: int
    trace_id: Optional[str]


class WorkflowStage(str, Enum):
    """工作流阶段"""
    START = "start"
    RESEARCH = "research"
    WRITE = "write"
    EDIT = "edit"
    SEO = "seo"
    IMAGE = "image"
    CMS = "cms"
    EVOLVE = "evolve"    # 自演化节点（Agent根据数据自我优化）
    END = "end"
    ERROR = "error"


class MultiAgentWorkflow:
    """多Agent内容生产工作流 - LangGraph实现"""
    
    def __init__(self, config_dir: str = "agents", image_mode: str = "plan_only"):
        """
        初始化工作流
        
        Args:
            config_dir: Agent配置目录
        """
        self.config_dir = config_dir
        self.image_mode = (image_mode or "plan_only").strip().lower()
        try:
            self.llm = ChatOpenAI(model="gpt-4o", temperature=0.4)
        except Exception:
            self.llm = None
        self.workflow = None
        self.compiled_workflow = None
        
        # 加载所有Agent配置
        self.agent_configs = self._load_all_configs()
        
        # 构建工作流
        self._build_workflow()

    def _trace_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def _topic_input_id(self, state: Dict[str, Any]) -> str:
        topic = state.get("topic") if isinstance(state, dict) else {}
        if isinstance(topic, dict):
            return str(topic.get("id") or topic.get("title") or "")
        return ""

    def _workflow_error(self, stage: str, exc: Exception, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        input_id = self._topic_input_id(state or {})
        trace_id = str((state or {}).get("trace_id") or self._trace_id())
        logger.exception("workflow_error stage=%s input_id=%s trace_id=%s", stage, input_id, trace_id)
        return {
            "stage": str(stage),
            "type": exc.__class__.__name__,
            "message": str(exc),
            "input_id": input_id,
            "trace_id": trace_id,
        }

    def _log_stage(
        self,
        stage: str,
        status: str,
        state: Optional[Dict[str, Any]] = None,
        *,
        level: int = logging.INFO,
        **fields: Any,
    ) -> None:
        payload = {
            "workflow": "langgraph",
            "stage": str(stage),
            "status": status,
            "input_id": self._topic_input_id(state or {}),
            "trace_id": str((state or {}).get("trace_id") or self._trace_id()),
        }
        payload.update({k: v for k, v in fields.items() if v not in (None, "", [], {})})
        logger.log(level, "workflow_event %s", json.dumps(payload, ensure_ascii=False, default=str))

    def _run_async_sync(self, coro: Any, *, stage: str, state: Optional[Dict[str, Any]] = None) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        if hasattr(coro, "close"):
            coro.close()
        input_id = self._topic_input_id(state or {})
        trace_id = str((state or {}).get("trace_id") or self._trace_id())
        raise RuntimeError(
            f"running_event_loop_not_supported_for_sync_workflow stage={stage} input_id={input_id} trace_id={trace_id}"
        )

    def _normalize_image_result(self, image_result: Any, *, generated: bool = False) -> Dict[str, Any]:
        raw = image_result if isinstance(image_result, dict) else {}
        inline_images = []
        for item in raw.get("inline_images") or []:
            if not isinstance(item, dict):
                continue
            inline_images.append(
                {
                    "url": str(item.get("url") or ""),
                    "alt": str(item.get("alt") or ""),
                    "prompt": str(item.get("prompt") or ""),
                    "position": str(item.get("position") or ""),
                }
            )
        license_payload = raw.get("license") or {}
        if not isinstance(license_payload, dict):
            license_payload = {}
        return {
            "featured_image_url": str(raw.get("featured_image_url") or ""),
            "featured_alt": str(raw.get("featured_alt") or ""),
            "featured_prompt": str(raw.get("featured_prompt") or ""),
            "inline_images": inline_images,
            "license": {
                "source": str(license_payload.get("source") or ("generated" if generated else "planned")),
                "provider": str(license_payload.get("provider") or "openai"),
            },
        }
    
    def _load_all_configs(self) -> Dict:
        """加载所有Agent配置"""
        configs = {}
        agent_names = [
            "topic_agent",
            "research_agent",
            "writer_agent",
            "editor_agent",
            "seo_agent",
            "image_agent",
            "cms_agent"
        ]
        
        for agent_name in agent_names:
            config_path = os.path.join(self.config_dir, agent_name, "config.yaml")
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    configs[agent_name] = yaml.safe_load(f)
        
        return configs
    
    def _build_workflow(self):
        """构建LangGraph工作流"""
        
        # 创建状态图
        workflow = StateGraph(PipelineState)
        
        # 添加节点
        workflow.add_node(WorkflowStage.RESEARCH, self._research_node)
        workflow.add_node(WorkflowStage.WRITE, self._write_node)
        workflow.add_node(WorkflowStage.EDIT, self._edit_node)
        workflow.add_node(WorkflowStage.SEO, self._seo_node)
        workflow.add_node(WorkflowStage.IMAGE, self._image_node)
        workflow.add_node(WorkflowStage.CMS, self._cms_node)
        workflow.add_node(WorkflowStage.EVOLVE, self._evolve_node)
        workflow.add_node(WorkflowStage.ERROR, self._error_node)

        # 设置入口
        workflow.set_entry_point(WorkflowStage.RESEARCH)

        # 添加边（完全自主顺序执行 + 自演化反馈）

        # 调研 → 写作 → 编辑 → SEO → 图片 → CMS → 自演化 → 结束
        workflow.add_edge(WorkflowStage.RESEARCH, WorkflowStage.WRITE)
        workflow.add_edge(WorkflowStage.WRITE, WorkflowStage.EDIT)
        workflow.add_edge(WorkflowStage.EDIT, WorkflowStage.SEO)
        workflow.add_edge(WorkflowStage.SEO, WorkflowStage.IMAGE)
        workflow.add_edge(WorkflowStage.IMAGE, WorkflowStage.CMS)
        workflow.add_edge(WorkflowStage.CMS, WorkflowStage.EVOLVE)

        # 自演化节点结束后，将演化结果写回（供下次运行时使用）
        # 注意：单次运行内反馈循环通过shared DB实现；跨运行反馈由scheduler在下次选题时注入
        workflow.add_edge(WorkflowStage.EVOLVE, END)

        # 错误 → 结束
        workflow.add_edge(WorkflowStage.ERROR, END)
        
        # 编译工作流
        self.workflow = workflow
        self.compiled_workflow = workflow.compile()
        
        logger.info("workflow=langgraph stage=build status=compiled")
    
    def _research_node(self, state: PipelineState) -> PipelineState:
        """
        调研节点：
        - 输入：state.topic（选题）
        - 过程：读取 agents/research_agent/prompt.md → 填充变量 → LLM → 解析 JSON
        - 输出：state.research_result（结构化调研包）
        """
        self._log_stage(WorkflowStage.RESEARCH, "start", state)
        try:
            topic = state["topic"]
            
            # 读取prompt模板
            prompt_path = os.path.join(self.config_dir, "research_agent", "prompt.md")
            with open(prompt_path, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
            
            # 填充模板
            # 说明：prompt.md 里用 {title}/{primary_keyword}/{content_type} 这样的占位符
            prompt = prompt_template.replace("{title}", topic.get("title", ""))
            prompt = prompt.replace("{primary_keyword}", topic.get("primary_keyword", ""))
            prompt = prompt.replace("{content_type}", topic.get("content_type", ""))
            
            # 调用LLM
            # 说明：这里把 SystemMessage 当作“角色设定”，HumanMessage 当作“任务指令”
            messages = [
                SystemMessage(content="你是专业调研员，擅长收集和组织资料。"),
                HumanMessage(content=prompt)
            ]
            
            response = self.llm.invoke(messages)
            
            # 解析结果
            # 约定：prompt 输出必须是 JSON 字符串，否则这里会抛异常并进入 ERROR 节点
            raw = response.content if isinstance(response.content, str) else str(response.content)
            text = raw.strip()
            if "```" in text:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1 and end > start:
                    text = text[start : end + 1]
            research_result = json.loads(text)
            if isinstance(research_result, dict):
                if "background_info" in research_result and "background" not in research_result:
                    research_result["background"] = research_result.get("background_info")
                if "key_statistics" in research_result and "statistics" not in research_result:
                    research_result["statistics"] = research_result.get("key_statistics")
                if "case_studies" in research_result and "cases" not in research_result:
                    research_result["cases"] = research_result.get("case_studies")
                if "expert_quotes" in research_result and "quotes" not in research_result:
                    research_result["quotes"] = research_result.get("expert_quotes")
                if "detailed_outline" in research_result and "outline" not in research_result:
                    research_result["outline"] = research_result.get("detailed_outline")
                if "sources" not in research_result:
                    research_result["sources"] = []
            
            # 更新状态
            state["research_result"] = research_result
            state["current_stage"] = WorkflowStage.RESEARCH
            state["error"] = None
            self._log_stage(
                WorkflowStage.RESEARCH,
                "success",
                state,
                statistics_count=len(research_result.get("statistics", [])),
                cases_count=len(research_result.get("cases", [])),
            )
        except Exception as e:
            state["error"] = self._workflow_error(WorkflowStage.RESEARCH, e, state)
            state["current_stage"] = WorkflowStage.ERROR
        
        return state
    
    def _write_node(self, state: PipelineState) -> PipelineState:
        """
        写作节点：
        - 输入：state.topic + state.research_result
        - 过程：读取 agents/writer_agent/prompt.md → 填充变量（含 research_materials）→ LLM → 解析 JSON
        - 输出：state.write_result（文章草稿 + 统计/SEO分析等）
        """
        self._log_stage(WorkflowStage.WRITE, "start", state)
        try:
            topic = state["topic"]
            research_result = state["research_result"]

            from agents.writer_agent import WriterAgent

            agent = WriterAgent(
                config_path=os.path.join(self.config_dir, "writer_agent", "config.yaml"),
                prompt_path=os.path.join(self.config_dir, "writer_agent", "prompt.md"),
                llm=self.llm,
            )
            outline = None
            if isinstance(research_result, dict):
                outline = research_result.get("outline") or research_result.get("detailed_outline") or research_result.get("hierarchy_outline")

            write_result = self._run_async_sync(
                agent.execute(
                    topic=topic if isinstance(topic, dict) else {},
                    outline=outline if isinstance(outline, dict) else None,
                    materials=research_result if isinstance(research_result, dict) else {},
                    brand_config=state.get("brand_config") if isinstance(state.get("brand_config"), dict) else {},
                    dry_run=True,
                ),
                stage=str(WorkflowStage.WRITE),
                state=state,
            )
            
            # 更新状态
            state["write_result"] = write_result
            state["current_stage"] = WorkflowStage.WRITE
            state["error"] = None
            self._log_stage(
                WorkflowStage.WRITE,
                "success",
                state,
                word_count=(write_result.get("statistics", {}) or {}).get("word_count"),
            )
        except Exception as e:
            state["error"] = self._workflow_error(WorkflowStage.WRITE, e, state)
            state["current_stage"] = WorkflowStage.ERROR
        
        return state
    
    def _edit_node(self, state: PipelineState) -> PipelineState:
        """
        编辑节点：
        - 输入：state.write_result（文章草稿）
        - 过程：读取 agents/editor_agent/prompt.md → 注入 title/content → LLM → 解析 JSON
        - 输出：state.edit_result（审校后文章 + 质量评分/问题清单）
        """
        self._log_stage(WorkflowStage.EDIT, "start", state)
        try:
            write_result = state["write_result"]

            from agents.editor_agent import EditorAgent

            draft_article = write_result.get("article", {}) if isinstance(write_result, dict) else {}
            topic = state.get("topic") or {}
            agent = EditorAgent(
                config_path=os.path.join(self.config_dir, "editor_agent", "config.yaml"),
                prompt_path=os.path.join(self.config_dir, "editor_agent", "prompt.md"),
            )
            edit_result = self._run_async_sync(
                agent.execute(article=draft_article, topic=topic, dry_run=True),
                stage=str(WorkflowStage.EDIT),
                state=state,
            )
            
            # 更新状态
            state["edit_result"] = edit_result
            state["current_stage"] = WorkflowStage.EDIT
            state["error"] = None
            quality_score = edit_result.get("quality_score") if isinstance(edit_result, dict) else None
            if isinstance(quality_score, dict):
                quality_score = quality_score.get("overall")
            self._log_stage(WorkflowStage.EDIT, "success", state, quality_score=quality_score)
        except Exception as e:
            state["error"] = self._workflow_error(WorkflowStage.EDIT, e, state)
            state["current_stage"] = WorkflowStage.ERROR
        
        return state
    
    def _seo_node(self, state: PipelineState) -> PipelineState:
        """
        SEO 优化节点：
        - 输入：state.edit_result.article（优先）或 state.write_result.article
        - 过程：调用 SEOAgent（KeywordAnalyzer/MetaGenerator/SchemaGenerator）生成结构化结果
        - 输出：state.seo_result（结构化 SEO 产物）
        """
        self._log_stage(WorkflowStage.SEO, "start", state)
        try:
            topic = state.get("topic") or {}
            category = topic.get("category") or topic.get("content_type") or ""

            edit_result = state.get("edit_result") or {}
            write_result = state.get("write_result") or {}

            article = {}
            if isinstance(edit_result, dict) and isinstance(edit_result.get("article"), dict):
                article = edit_result.get("article") or {}
            elif isinstance(write_result, dict) and isinstance(write_result.get("article"), dict):
                article = write_result.get("article") or {}

            title = article.get("title") or topic.get("title") or ""
            content = article.get("content_md") or article.get("content") or ""
            url_path = article.get("slug") or ""

            from agents.seo_agent import SEOAgent

            agent = SEOAgent(config_path=os.path.join(self.config_dir, "seo_agent", "config.yaml"))
            seo_result = self._run_async_sync(
                agent.execute(
                    article={
                        "title": title,
                        "content_md": content,
                        "meta_description": article.get("meta_description")
                        or (article.get("meta") or {}).get("meta_description")
                        or "",
                        "slug": url_path,
                    },
                    topic=topic,
                    page_info={"slug": url_path, "category": category},
                ),
                stage=str(WorkflowStage.SEO),
                state=state,
            )

            if not isinstance(seo_result, dict):
                seo_result = {}

            seo_result.setdefault("optimized_article", {"title": title, "content": content})
            seo_result.setdefault("meta_title", "")
            seo_result.setdefault("meta_description", "")
            seo_result.setdefault("og_tags", {})
            seo_result.setdefault("twitter_tags", {})
            seo_result.setdefault("schema_json", {})
            seo_result.setdefault("internal_links", [])
            seo_result.setdefault("seo_report", {})
            seo_result.setdefault("improvement_suggestions", [])

            state["seo_result"] = seo_result
            state["current_stage"] = WorkflowStage.SEO
            state["error"] = None
            self._log_stage(WorkflowStage.SEO, "success", state)
        except Exception as e:
            state["error"] = self._workflow_error(WorkflowStage.SEO, e, state)
            state["current_stage"] = WorkflowStage.ERROR
        return state
    
    def _image_node(self, state: PipelineState) -> PipelineState:
        """
        图片处理节点：
        - 输入：SEO 优化稿 + 选题信息
        - 输出：与 hybrid_workflow 对齐的 image_result
        """
        self._log_stage(WorkflowStage.IMAGE, "start", state)
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
            messages = [
                SystemMessage(content="你是配图设计师，必须输出纯JSON，不要输出代码块。"),
                HumanMessage(content=prompt),
            ]
            response = self.llm.invoke(messages)
            raw = response.content if isinstance(response.content, str) else str(response.content)
            text = raw.strip()
            if "```" in text:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1 and end > start:
                    text = text[start : end + 1]
            image_result = self._normalize_image_result(json.loads(text))

            if self.image_mode == "generate" and isinstance(image_result, dict):
                alt_gen = AltTextGenerator()

                async def _fill() -> Dict[str, Any]:
                    generator = ImageGenerator()
                    try:
                        plan = dict(image_result)
                        featured_prompt = str(plan.get("featured_prompt") or "").strip()
                        if featured_prompt:
                            img = await generator.generate(prompt=featured_prompt)
                            url = ""
                            if img.get("success") and img.get("images"):
                                url = str((img["images"][0].get("url") or "")).strip()
                            plan["featured_image_url"] = url
                            if not str(plan.get("featured_alt") or "").strip():
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
                                out = dict(item)
                                p = str(out.get("prompt") or "").strip()
                                if p:
                                    img = await generator.generate(prompt=p)
                                    url = ""
                                    if img.get("success") and img.get("images"):
                                        url = str((img["images"][0].get("url") or "")).strip()
                                    out["url"] = url
                                    if not str(out.get("alt") or "").strip():
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

                image_result = self._normalize_image_result(
                    self._run_async_sync(_fill(), stage=str(WorkflowStage.IMAGE), state=state),
                    generated=True,
                )

            state["image_result"] = image_result
            state["current_stage"] = WorkflowStage.IMAGE
            state["error"] = None
            self._log_stage(
                WorkflowStage.IMAGE,
                "success",
                state,
                inline_image_count=len(image_result.get("inline_images", [])) if isinstance(image_result, dict) else 0,
                image_mode=self.image_mode,
            )
        except Exception as e:
            state["error"] = self._workflow_error(WorkflowStage.IMAGE, e, state)
            state["current_stage"] = WorkflowStage.ERROR
        return state
    
    def _cms_node(self, state: PipelineState) -> PipelineState:
        """
        CMS 发布节点：
        - 输入：最终稿 + SEO 结果 + 图片结果
        - 调用 CMSAgent 生成真实发布结果或 dry-run 结果
        """
        self._log_stage(WorkflowStage.CMS, "start", state)
        try:
            topic = state.get("topic") or {}
            edit_result = state.get("edit_result") or {}
            write_result = state.get("write_result") or {}
            seo_result = state.get("seo_result") or {}

            article = {}
            if isinstance(edit_result, dict) and isinstance(edit_result.get("article"), dict):
                article = edit_result.get("article") or {}
            elif isinstance(write_result, dict) and isinstance(write_result.get("article"), dict):
                article = write_result.get("article") or {}
            else:
                article = {"title": topic.get("title") or "", "content": ""}

            optimized_article = seo_result.get("optimized_article") or {}
            if not isinstance(optimized_article, dict):
                optimized_article = {}
            content_value = (
                optimized_article.get("content")
                or article.get("content_html")
                or article.get("content_md")
                or article.get("content")
                or ""
            )
            article = {
                "title": optimized_article.get("title") or article.get("title") or topic.get("title") or "",
                "content_html": article.get("content_html") or "",
                "content_md": article.get("content_md") or "",
                "content": content_value,
                "meta": {
                    "meta_title": seo_result.get("meta_title")
                    or ((article.get("meta") or {}).get("meta_title") if isinstance(article.get("meta"), dict) else "")
                    or article.get("title")
                    or topic.get("title")
                    or "",
                    "meta_description": seo_result.get("meta_description")
                    or ((article.get("meta") or {}).get("meta_description") if isinstance(article.get("meta"), dict) else "")
                    or article.get("meta_description")
                    or "",
                    "og_tags": seo_result.get("og_tags") or {},
                    "twitter_tags": seo_result.get("twitter_tags") or {},
                    "schema_json": seo_result.get("schema_json") or {},
                },
                "slug": article.get("slug") or optimized_article.get("slug") or "",
                "featured_image_url": article.get("featured_image_url") or "",
            }
            page_info = {
                "category": (topic.get("category") or topic.get("content_type")),
                "tags": topic.get("secondary_keywords") or [],
                "slug": (article.get("slug") or ""),
            }

            img_payload = self._normalize_image_result(state.get("image_result") or {})
            images = {
                "featured_image_url": article.get("featured_image_url") or img_payload.get("featured_image_url") or "",
                "featured_alt": img_payload.get("featured_alt") or "",
            }

            agent = CMSAgent()
            cms_result = self._run_async_sync(
                agent.execute(article=article, page_info=page_info, images=images),
                stage=str(WorkflowStage.CMS),
                state=state,
            )
            state["cms_result"] = cms_result
            state["current_stage"] = WorkflowStage.CMS
            state["error"] = None
            self._log_stage(
                WorkflowStage.CMS,
                "success",
                state,
                publish_status=(cms_result or {}).get("status"),
                article_url=(cms_result or {}).get("article_url"),
            )
        except Exception as e:
            state["error"] = self._workflow_error(WorkflowStage.CMS, e, state)
            state["current_stage"] = WorkflowStage.ERROR
        return state
    
    def _evolve_node(self, state: PipelineState) -> PipelineState:
        """
        自演化节点（闭环）：
        - 输入：本轮发布结果（理论上应包含文章 URL / topic_id）
        - 获取：历史性能数据（PV/CTR/跳出率/排名等）
        - 输出：下一轮可用于选题/SEO 的关键词或策略建议

        这里的实现是“示意版”，重点展示：工作流在 END 前可以加入反馈节点。
        """
        self._log_stage(WorkflowStage.EVOLVE, "start", state)
        try:
            # 从数据库获取历史性能数据
            perf = self._fetch_performance_data(state.get("topic", {}).get("id"))
            state["performance_data"] = perf

            # 基于性能数据演化关键词
            evolved = self._evolve_keywords(
                current_keywords=state.get("topic", {}).get("keywords", []),
                performance=perf
            )
            state["evolved_keywords"] = evolved

            self._log_stage(
                WorkflowStage.EVOLVE,
                "success",
                state,
                page_views=perf.get("page_views", 0),
                bounce_rate=perf.get("bounce_rate", "N/A"),
                evolved_keywords=evolved[:5],
            )
        except Exception as e:
            self._log_stage(
                WorkflowStage.EVOLVE,
                "warning",
                state,
                level=logging.WARNING,
                error_type=e.__class__.__name__,
                error_message=str(e),
            )
            state["evolved_keywords"] = []
            state["performance_data"] = {}

        state["current_stage"] = WorkflowStage.EVOLVE
        return state

    def _fetch_performance_data(self, topic_id: str) -> Dict[str, Any]:
        """从数据库获取历史性能数据（模拟实现）"""
        # 实际实现：从 MySQL 查询 analytics 表
        return {
            "page_views": 0,
            "bounce_rate": "N/A",
            "avg_time_on_page": "N/A",
            "organic_search": 0
        }

    def _evolve_keywords(self, current_keywords: List[str], performance: Dict) -> List[str]:
        """基于性能数据演化关键词（模拟实现）"""
        # 实际实现：调用DataAgent分析数据后生成优化建议
        # 这里简化处理，返回优化后的关键词列表
        return current_keywords[:3] if current_keywords else []
    
    def _error_node(self, state: PipelineState) -> PipelineState:
        """错误处理节点"""
        self._log_stage(WorkflowStage.ERROR, "error", state, level=logging.ERROR, error=state.get("error"))
        return state
    

    
    def run_workflow(self, topic: Dict[str, Any]) -> Dict[str, Any]:
        """
        运行工作流
        
        Args:
            topic: 选题信息
            
        Returns:
            执行结果
        """
        # 初始状态（人类通过 brand_config 和 quality_threshold 参与配置，Agent 全自主执行）
        # brand_config 里一般放：
        # - 品牌指南路径/内容
        # - 禁用词、语气、段落长度等风格约束
        initial_state = PipelineState(
            topic=topic,
            brand_config={"brand_guide": "config/brand_guidelines.yaml"},  # 人类配置
            quality_threshold=0.8,                                         # 人类配置
            research_result=None,
            write_result=None,
            edit_result=None,
            seo_result=None,
            image_result=None,
            cms_result=None,
            evolved_keywords=None,
            performance_data=None,
            current_stage=WorkflowStage.START,
            error=None,
            retry_count=0,
            trace_id=self._trace_id(),
        )
        self._log_stage(WorkflowStage.START, "start", initial_state)
        
        # 执行工作流
        result = self.compiled_workflow.invoke(initial_state)
        self._log_stage(
            WorkflowStage.END if result.get("error") is None else WorkflowStage.ERROR,
            "success" if result.get("error") is None else "error",
            result,
        )
        
        return {
            "status": "success" if result.get("error") is None else "error",
            "result": result,
            "timestamp": datetime.now().isoformat()
        }


def main():
    """主函数 - 演示用法"""
    
    # 示例选题
    topic = {
        "title": "EMBA如何选择：5个关键维度",
        "primary_keyword": "EMBA选择",
        "secondary_keywords": ["EMBA项目", "EMBA报考条件"],
        "content_type": "guide",
        "min_word_count": 2000,
        "max_word_count": 4000
    }
    
    # 创建并运行工作流
    workflow = MultiAgentWorkflow(config_dir="../agents")
    result = workflow.run_workflow(topic)
    
    logger.info("workflow=langgraph stage=main_demo result=%s", json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
