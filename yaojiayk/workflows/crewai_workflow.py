#!/usr/bin/env python3
"""
CrewAI工作流实现 - 多Agent内容生产流水线
版本: v1.0
创建时间: 2026-05-13

本文件在项目中的角色：
- “编排层”的参考实现：通过 CrewAI 的 Agent/Task/Crew 把多阶段工作串起来
- 与 LangGraph 版本不同点在于：CrewAI 更像“声明式任务队列”，LangGraph 更像“可分支可循环的状态机”

当前实现侧重展示“内容生产流水线”的最小链路：
Research → Write → Edit → SEO → Image → CMS

调度器（scheduler/）会通过导入本文件底部的 run_*_workflow 便捷函数来触发任务，
这些便捷函数的实现以“可读性/可演示”为优先，很多外部 API 调用仍是占位或模拟实现。
"""

import os
import json
import yaml
import asyncio
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
from dataclasses import asdict, is_dataclass

# CrewAI 相关导入
from crewai import Agent, Task, Crew, Process
from crewai.tools import tool

# 自定义工具导入
from agents.topic_agent.tools.keyword_research import KeywordResearchTool
from agents.topic_agent.tools.trend_detection import TrendDetectionTool
from agents.topic_agent.tools.serp_analysis import SERPAnalysisTool
from agents.topic_agent.tools import get_keyword_research_tool, get_trend_detection_tool, get_serp_analysis_tool
from agents.cms_agent.tools.cms_client import get_cms_client_tool
from agents.cms_agent.tools.media_uploader import get_media_uploader_tool
from agents.image_agent.tools import get_image_generator_tool, get_alt_text_generator_tool
from agents.research_agent.tools.data_collector import get_data_collector_tool
from agents.research_agent.tools.citation_formatter import get_citation_formatter_tool
from agents.seo_agent.tools import get_keyword_analyzer_tool, get_meta_generator_tool, get_schema_generator_tool
from agents.writer_agent.tools import get_readability_checker_tool

logger = logging.getLogger(__name__)


class MultiAgentContentPipeline:
    """多Agent内容生产流水线 - CrewAI实现"""
    
    def __init__(self, config_dir: str = "agents"):
        """
        初始化流水线
        
        Args:
            config_dir: Agent配置目录
        """
        self.config_dir = config_dir
        self.agents = {}
        self.tasks = {}
        self.crew = None
        
        # 加载所有Agent配置
        self._load_agent_configs()
        
        # 创建Agent实例
        self._create_agents()
    
    def _load_agent_configs(self):
        """加载所有Agent的配置"""
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
                    config = yaml.safe_load(f)
                    self.agents[agent_name] = {
                        "config": config,
                        "skill_path": os.path.join(self.config_dir, agent_name, "SKILL.md"),
                        "prompt_path": os.path.join(self.config_dir, agent_name, "prompt.md")
                    }
                    logger.info("workflow=crewai stage=config_load agent=%s status=loaded", agent_name)
            else:
                logger.warning("workflow=crewai stage=config_load agent=%s status=missing path=%s", agent_name, config_path)
    
    def _create_agents(self):
        """创建CrewAI Agent实例"""
        
        # 1. 选题Agent
        if "topic_agent" in self.agents:
            topic_config = self.agents["topic_agent"]["config"]
            
            self.topic_agent = Agent(
                role='选题策划师',
                goal='研究关键词趋势，策划高价值的内容选题',
                backstory='你是一位资深的内容策略专家，擅长通过数据分析发现高价值的内容机会。',
                verbose=True,
                allow_delegation=False,
                llm=self._get_llm(topic_config),
                tools=[get_keyword_research_tool(), get_trend_detection_tool(), get_serp_analysis_tool()],
            )
            logger.info("workflow=crewai stage=create_agent agent=topic_agent status=created")
        
        # 2. 调研Agent
        if "research_agent" in self.agents:
            research_config = self.agents["research_agent"]["config"]
            
            self.research_agent = Agent(
                role='调研研究员',
                goal='为文章收集全面的背景资料和素材',
                backstory='你是一位专业的研究员，擅长快速收集和整理各类资料。',
                verbose=True,
                allow_delegation=False,
                llm=self._get_llm(research_config),
                tools=[get_data_collector_tool(), get_citation_formatter_tool()],
            )
            logger.info("workflow=crewai stage=create_agent agent=research_agent status=created")
        
        # 3. 写作Agent
        if "writer_agent" in self.agents:
            writer_config = self.agents["writer_agent"]["config"]
            
            self.writer_agent = Agent(
                role='高级撰稿人',
                goal='撰写高质量、SEO友好的原创文章',
                backstory='''你是一位经验丰富的内容创作者，擅长撰写专业、易懂且有实用价值的文章。''',
                verbose=True,
                allow_delegation=False,
                llm=self._get_llm(writer_config),
                tools=[get_readability_checker_tool()],
            )
            logger.info("workflow=crewai stage=create_agent agent=writer_agent status=created")
        
        # 4. 编辑Agent
        if "editor_agent" in self.agents:
            editor_config = self.agents["editor_agent"]["config"]
            from agents.editor_agent.tools.grammar_checker import get_grammar_checker_tool
            
            self.editor_agent = Agent(
                role='审校编辑',
                goal='审校和润色文章，确保质量和品牌一致性',
                backstory='你是一位严谨的编辑，专注于提升文章质量和可读性。',
                verbose=True,
                allow_delegation=False,
                tools=[get_grammar_checker_tool()],
                llm=self._get_llm(editor_config)
            )
            logger.info("workflow=crewai stage=create_agent agent=editor_agent status=created")
        
        # 5. SEO Agent
        if "seo_agent" in self.agents:
            seo_config = self.agents["seo_agent"]["config"]
            
            self.seo_agent = Agent(
                role='SEO优化专家',
                goal='优化文章搜索引擎可见性，提升搜索排名',
                backstory='你是一位SEO专家，精通搜索引擎优化策略和技术。',
                verbose=True,
                allow_delegation=False,
                llm=self._get_llm(seo_config),
                tools=[get_keyword_analyzer_tool(), get_meta_generator_tool(), get_schema_generator_tool()],
            )
            logger.info("workflow=crewai stage=create_agent agent=seo_agent status=created")
        
        # 6. 图片Agent
        if "image_agent" in self.agents:
            image_config = self.agents["image_agent"]["config"]
            
            self.image_agent = Agent(
                role='配图设计师',
                goal='为文章生成或选择合适的配图',
                backstory='你是一位视觉设计师，擅长创作符合文章主题的配图。',
                verbose=True,
                allow_delegation=False,
                llm=self._get_llm(image_config),
                tools=[get_image_generator_tool(), get_alt_text_generator_tool()],
            )
            logger.info("workflow=crewai stage=create_agent agent=image_agent status=created")
        
        # 7. CMS Agent
        if "cms_agent" in self.agents:
            cms_config = self.agents["cms_agent"]["config"]
            
            self.cms_agent = Agent(
                role='CMS发布员',
                goal='将文章准确发布到CMS系统',
                backstory='你是一位技术熟练的CMS操作员，确保内容正确发布。',
                verbose=True,
                allow_delegation=False,
                llm=self._get_llm(cms_config),
                tools=[get_cms_client_tool(), get_media_uploader_tool()],
            )
            logger.info("workflow=crewai stage=create_agent agent=cms_agent status=created")
    
    def _get_llm(self, config: Dict) -> str:
        """
        从配置中获取LLM模型字符串
        
        Args:
            config: Agent配置
            
        Returns:
            LLM模型标识符
        """
        llm_config = config.get("llm", {})
        provider = llm_config.get("provider", "openai")
        model = llm_config.get("model", "gpt-4o")
        
        if provider == "openai":
            return model
        elif provider == "deepseek":
            return f"deepseek/{model}"
        else:
            return model
    
    def create_content_pipeline(self, topic: Dict[str, Any]) -> Crew:
        """
        创建内容生产流水线
        
        Args:
            topic: 选题信息，包含title, primary_keyword, content_type等
            
        Returns:
            CrewAI Crew实例
        """
        
        # 任务1: 调研
        research_task = Task(
            description=f'''
            为以下选题进行深度调研：
            
            标题: {topic.get('title')}
            主关键词: {topic.get('primary_keyword')}
            内容类型: {topic.get('content_type')}
            
            请收集：
            1. 背景资料
            2. 关键数据（需注明来源）
            3. 案例素材
            4. 专家观点
            5. 引用来源

            规则：
            - 必须调用工具 data_collector 获取可追溯来源（至少包含 news_articles / industry_reports 的 items）
            - 需要引用列表时，必须调用工具 citation_formatter 生成引用条目
            
            输出格式：只输出 JSON（字段必须齐全）：background/statistics/cases/quotes/sources/citations/outline
            ''',
            agent=self.research_agent,
            expected_output="包含背景资料、数据、案例、引用的JSON对象"
        )
        
        # 任务2: 写作
        write_task = Task(
            description=f'''
            根据调研素材撰写文章：
            
            选题: {topic.get('title')}
            主关键词: {topic.get('primary_keyword')}
            次关键词: {topic.get('secondary_keywords')}
            内容类型: {topic.get('content_type')}
            
            要求：
            1. 字数{topic.get('min_word_count', 1200)}-{topic.get('max_word_count', 4000)}字
            2. 关键词密度1-2.5%
            3. 结构清晰，可读性强
            4. 提供实用价值
            5. 文末必须包含“## 参考来源”小节，并至少列出 1 条来自调研结果 sources/citations 的可回链 URL
            6. 必须调用工具 readability_checker 检查正文，并把结果写入 quality_checks.readability
            
            最终只输出 JSON（字段必须齐全）：
            - article.title / article.content_md / article.meta_description
            - seo_analysis
            - internal_links
            - image_alt_texts
            - statistics.word_count / statistics.reading_time_minutes
            - quality_checks
            - warnings
            ''',
            agent=self.writer_agent,
            context=[research_task],
            expected_output="包含文章内容和SEO分析的JSON对象"
        )
        
        # 任务3: 编辑审校
        edit_task = Task(
            description='''
            对文章进行专业审校和润色：
            
            审校维度：
            1. 内容审校（事实、逻辑、论证）
            2. 语言审校（语法、表达、风格）
            3. 格式审校（Markdown、图片、链接）
            4. SEO审校（关键词、Meta）
            
            规则：
            - 必须看到正文并基于正文审校。
            - 必须调用工具 grammar_checker，并把工具结果合并到最终 JSON。

            输出 JSON（字段必须齐全）：
            {
              "article": {"title":"...","content_md":"...","meta_description":"..."},
              "quality_score": {"overall": 85, "dimensions": {}},
              "issues_found": [],
              "polishing_notes": [],
              "approval_status": "approved"
            }
            ''',
            agent=self.editor_agent,
            context=[write_task],
            expected_output="包含审校后文章和质量评分的JSON对象"
        )
        
        # 任务4: SEO优化
        seo_task = Task(
            description='''
            对文章进行全面的SEO优化：
            
            优化内容：
            1. 关键词优化（密度、分布、LSI词）
            2. Meta标签优化（Title、Description）
            3. 内容结构优化（标题层级、列表使用）
            4. Schema标记生成
            5. 内链建议
            
            规则：
            - 必须调用工具 keyword_analyzer / meta_generator / schema_generator
            - 最终输出必须是 JSON（字段必须齐全）：
              {
                "optimized_article": {"title":"...","content":"..."},
                "meta_title": "...",
                "meta_description": "...",
                "og_tags": {},
                "twitter_tags": {},
                "schema_json": {},
                "internal_links": [],
                "seo_report": {},
                "improvement_suggestions": []
              }
            ''',
            agent=self.seo_agent,
            context=[edit_task],
            expected_output="包含SEO优化结果和报告的JSON对象"
        )
        
        # 任务5: 图片处理
        image_task = Task(
            description='''
            为文章生成或选择合适的配图：
            
            图片需求：
            1. 封面图（1张，16:9或1.91:1）
            2. 文中插图（可选，2-4张）
            
            规则：
            - 必须调用工具 image_generator 生成图片（返回 url 或 b64_json）
            - 必须调用工具 alt_text_generator 生成 alt（language 建议用 auto）
            - 最终输出必须是 JSON（字段必须齐全）：
              {
                "featured_image_url": "...",
                "featured_alt": "...",
                "featured_prompt": "...",
                "inline_images": [
                  {"url":"...","alt":"...","prompt":"...","position":"..."}
                ],
                "license": {"source":"generated","provider":"openai"}
              }
            ''',
            agent=self.image_agent,
            context=[seo_task],
            expected_output="包含图片URL和alt文本的JSON对象"
        )
        
        # 任务6: CMS发布
        cms_task = Task(
            description='''
            将优化后的文章发布到CMS系统：
            
            发布前检查：
            1. 文章内容完整性
            2. 分类和标签设置
            3. 封面图设置
            4. SEO字段填充
            5. URL别名唯一性
            
            输出：
            1. 文章ID
            2. 文章URL
            3. 发布状态

            规则：
            - 默认不要真实发布，只输出结构化 payload（dry-run）。
            - 只有在明确需要真实发布时，才允许调用工具：
              1) 先调用 media_uploader(action="upload", file_url="...") 上传封面图，拿到 media_id 或 url
              2) 再调用 cms_client(action="create", title="...", content="...", status="draft", meta_title="...", meta_description="...") 创建文章
            ''',
            agent=self.cms_agent,
            context=[seo_task, image_task],
            expected_output="包含发布状态和URL的JSON对象"
        )
        
        # 创建Crew
        # process=Process.sequential 表示严格按 tasks 列表的顺序执行
        # verbose=True 会输出每一步的中间日志，便于理解运行过程
        self.crew = Crew(
            agents=[
                self.research_agent,
                self.writer_agent,
                self.editor_agent,
                self.seo_agent,
                self.image_agent,
                self.cms_agent
            ],
            tasks=[
                research_task,
                write_task,
                edit_task,
                seo_task,
                image_task,
                cms_task
            ],
            process=Process.sequential,  # 顺序执行
            verbose=True
        )
        
        return self.crew
    
    def run_pipeline(self, topic: Dict[str, Any]) -> Dict[str, Any]:
        """
        运行内容生产流水线
        
        Args:
            topic: 选题信息
            
        Returns:
            执行结果
        """
        run_id = datetime.now().strftime("%Y%m%d%H%M%S")
        logger.info(
            "workflow=crewai stage=start run_id=%s title=%s keyword=%s",
            run_id,
            (topic or {}).get("title") or "",
            (topic or {}).get("primary_keyword") or "",
        )
        
        # 创建流水线
        crew = self.create_content_pipeline(topic)
        
        # 执行
        result = crew.kickoff()
        
        logger.info("workflow=crewai stage=end run_id=%s status=success", run_id)
        
        return {
            "status": "success",
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
    
    # 创建流水线
    pipeline = MultiAgentContentPipeline(config_dir="../agents")
    
    # 运行流水线
    result = pipeline.run_pipeline(topic)
    
    logger.info("workflow=crewai stage=main_demo result=%s", json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()


def _normalize_result(obj: Any) -> Any:
    """
    把 dataclass 等对象转换成可 JSON 序列化的结构，方便调度器记录任务结果。
    """
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, list):
        return [_normalize_result(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _normalize_result(v) for k, v in obj.items()}
    return obj


async def run_topic_workflow(
    seed_keywords: Optional[List[str]] = None,
    min_search_volume: int = 100,
    max_kd: float = 35,
    auto_approve: bool = True,
    **_
) -> Dict[str, Any]:
    """
    调度器触发的“选题工作流”便捷入口（异步）。

    这里直接调用 TopicAgent 的关键词研究工具（模拟/占位实现），返回结构化结果。
    - seed_keywords：种子词列表（不传则使用默认示例）
    - auto_approve：保留字段，用于体现“自主运营”理念（当前逻辑未用到）
    """
    from agents.topic_agent import TopicAgent

    seed_keywords = seed_keywords or ["AI", "企业数字化"]
    if "min_volume" in _ and isinstance(_.get("min_volume"), (int, float)):
        min_search_volume = int(_.get("min_volume"))
    if "min_search_volume" in _ and isinstance(_.get("min_search_volume"), (int, float)):
        min_search_volume = int(_.get("min_search_volume"))
    if "max_keyword_difficulty" in _ and isinstance(_.get("max_keyword_difficulty"), (int, float)):
        max_kd = float(_.get("max_keyword_difficulty"))
    result = await TopicAgent().execute(keywords=seed_keywords, min_search_volume=min_search_volume, max_kd=max_kd, mode=_.get("mode"))

    return {
        "workflow": "topic",
        "auto_approve": auto_approve,
        "seed_keywords": seed_keywords,
        "result": _normalize_result(result),
        "timestamp": datetime.now().isoformat(),
        "note": "TopicAgent 已支持 mock/live 模式；live 模式需配置 SERPAPI_API_KEY 等环境变量。"
    }

async def run_topic_hybrid_workflow(
    seed_keywords: Optional[List[str]] = None,
    min_search_volume: int = 100,
    max_kd: float = 35,
    topic_limit: int = 5,
    auto_approve: bool = True,
    **_
) -> Dict[str, Any]:
    from agents.topic_agent import TopicAgent
    from yaojiayk.workflows.hybrid_workflow import HybridWorkflow
    from yaojiayk.workflows.topic_to_hybrid_adapter import select_best_topic, topic_item_to_hybrid_topic

    mode = _.get("topic_agent_mode") or _.get("mode")
    seed_keywords = seed_keywords or ["AI", "企业数字化"]
    if "min_volume" in _ and isinstance(_.get("min_volume"), (int, float)):
        min_search_volume = int(_.get("min_volume"))
    if "min_search_volume" in _ and isinstance(_.get("min_search_volume"), (int, float)):
        min_search_volume = int(_.get("min_search_volume"))
    if "max_keyword_difficulty" in _ and isinstance(_.get("max_keyword_difficulty"), (int, float)):
        max_kd = float(_.get("max_keyword_difficulty"))

    topic_agent_result = await TopicAgent(mode=mode).execute(
        keywords=seed_keywords,
        min_search_volume=min_search_volume,
        max_kd=max_kd,
        limit=topic_limit,
        mode=mode,
    )
    picked = select_best_topic(topic_agent_result)
    if not picked:
        return {
            "workflow": "topic_hybrid",
            "auto_approve": auto_approve,
            "seed_keywords": seed_keywords,
            "picked_topic": None,
            "topic_agent_result": _normalize_result(topic_agent_result),
            "hybrid_result": None,
            "timestamp": datetime.now().isoformat(),
        }
    hybrid_topic = topic_item_to_hybrid_topic(picked)

    def _run_hybrid():
        return HybridWorkflow(config_dir="agents", image_mode="plan_only").run(hybrid_topic)

    hybrid_result = await asyncio.to_thread(_run_hybrid)
    return {
        "workflow": "topic_hybrid",
        "auto_approve": auto_approve,
        "seed_keywords": seed_keywords,
        "picked_topic": _normalize_result(picked),
        "hybrid_topic": _normalize_result(hybrid_topic),
        "topic_agent_result": _normalize_result(topic_agent_result),
        "hybrid_result": _normalize_result(hybrid_result),
        "timestamp": datetime.now().isoformat(),
    }


async def run_data_workflow(report_type: str = "daily", **_) -> Dict[str, Any]:
    """
    调度器触发的“数据采集/报告生成”便捷入口（异步）。

    典型链路：
    - DataAgent 读取配置，按启用数据源采集并对比
    - 输出结构化报告、异常与建议
    """
    from agents.data_agent import DataAgent

    result = await DataAgent().execute(report_type=report_type)
    return {
        "workflow": "data",
        "report_type": report_type,
        "result": _normalize_result(result),
        "timestamp": datetime.now().isoformat(),
    }


async def run_competitor_workflow(**_) -> Dict[str, Any]:
    """
    调度器触发的“竞品监控”便捷入口（异步）。

    说明：
    - 竞品抓取/分析通常依赖 RSS/爬虫/SEO 工具 API
    - 当前仓库里相关工具多为占位实现，这里返回示意结构，便于你理解调度器如何串联任务
    """
    from agents.competitor_agent import CompetitorAgent

    agent = CompetitorAgent()
    return await agent.execute()


async def run_tech_seo_workflow(**_) -> Dict[str, Any]:
    """
    调度器触发的“技术 SEO 检查”便捷入口（异步，占位）。

    真实实现建议：
    - 抓取站点 sitemap/robots
    - 检测死链、结构化数据、Core Web Vitals 等
    - 输出可执行的修复建议与优先级
    """
    return {
        "workflow": "tech_seo",
        "timestamp": datetime.now().isoformat(),
        "note": "当前为占位实现；可新增 tech_seo_agent 或在 data_agent 下补充技术SEO检查工具。",
        "result": {"issues": [], "score": 0},
    }


async def run_monthly_review_workflow(**_) -> Dict[str, Any]:
    """
    调度器触发的“月度回顾”便捷入口（异步，占位）。

    真实实现建议：
    - 汇总当月内容产出、SEO 进展、流量趋势
    - 结合竞品动态生成下月策略与选题方向
    """
    return {
        "workflow": "monthly_review",
        "timestamp": datetime.now().isoformat(),
        "note": "当前为占位实现；建议在 DataAgent 的报告生成基础上补充趋势/策略总结。",
        "result": {},
    }
