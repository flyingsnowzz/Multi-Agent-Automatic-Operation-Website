#!/usr/bin/env python3
"""
多Agent自动运营网站 - 主入口文件

运行入口说明：
1) 本文件负责解析命令行参数，并选择使用 CrewAI 或 LangGraph 作为编排引擎
2) 两种引擎都会消费一个 topic（选题）对象，然后启动对应的工作流
3) 工作流内部会读取各 Agent 的 prompt 模板与配置文件（位于 agents/*）

你可以把 main.py 看作“本地演示/开发入口”：
- 生产环境一般由 scheduler/ 定时触发，或由 API 服务触发
- 这里提供了最短链路：准备输入 → 调用工作流 → 打印结果/日志
"""

import argparse
import asyncio
import json
try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*_, **__):
        return None

# 加载环境变量：
# - 如果当前目录有 .env，会把其中的 OPENAI_API_KEY 等变量加载到 os.environ
# - 本项目内很多工具/SDK 都依赖环境变量读取密钥与连接串
load_dotenv()

def run_crewai_pipeline(topic_title: str, keyword: str):
    """
    运行基于 CrewAI 的工作流。

    CrewAI 的核心思想是：
    - 先声明多个 Agent（角色、目标、工具、LLM）
    - 再声明多个 Task，并按顺序把 Task 交给对应的 Agent 执行
    """
    print(f"启动 CrewAI 工作流，选题：{topic_title}")
    
    # 工作流输入：topic（选题对象）
    # - title / primary_keyword 是最关键的字段
    # - 其余字段用于控制文章体裁、长度等约束
    topic = {
        "title": topic_title,
        "primary_keyword": keyword,
        "secondary_keywords": [],
        "content_type": "guide",
        "min_word_count": 1500,
        "max_word_count": 3000
    }
    
    # config_dir 指向 agents/，工作流会从 agents/*/config.yaml 加载配置
    from workflows.crewai_workflow import MultiAgentContentPipeline

    pipeline = MultiAgentContentPipeline(config_dir="agents")
    pipeline.run_pipeline(topic)
    print("CrewAI 工作流执行完毕。")

def run_langgraph_workflow(topic_title: str, keyword: str):
    """
    运行基于 LangGraph 的工作流。

    LangGraph 的核心思想是：
    - 把“工作流状态”定义成一个结构化对象（PipelineState）
    - 把每个阶段封装成一个 node(state)->state 函数
    - 用图结构描述 node 的连接关系（顺序/分支/循环/错误处理等）
    """
    print(f"启动 LangGraph 工作流，选题：{topic_title}")
    
    # 工作流输入：topic（选题对象）
    topic = {
        "title": topic_title,
        "primary_keyword": keyword,
        "secondary_keywords": [],
        "content_type": "guide",
        "min_word_count": 1500,
        "max_word_count": 3000
    }
    
    # config_dir 指向 agents/，工作流会读取各 Agent 的 prompt 模板与配置文件
    from workflows.langgraph_workflow import MultiAgentWorkflow

    workflow = MultiAgentWorkflow(config_dir="agents")
    workflow.run_workflow(topic)
    print("LangGraph 工作流执行完毕。")

def run_hybrid_workflow(topic_title: str, keyword: str):
    """
    运行混合架构工作流：
    - LangGraph 负责状态机/分支/重试
    - CrewAI 负责每个阶段的 Agent 执行与产出
    """
    print(f"启动 Hybrid 工作流，选题：{topic_title}")
    topic = {
        "title": topic_title,
        "primary_keyword": keyword,
        "secondary_keywords": [],
        "content_type": "guide",
        "min_word_count": 1500,
        "max_word_count": 3000
    }

    from workflows.hybrid_workflow import HybridWorkflow

    workflow = HybridWorkflow(config_dir="agents")
    workflow.run(topic)
    print("Hybrid 工作流执行完毕。")


def run_crawler_ingest(keyword: str):
    from workflows.crawler_workflow import run_crawler_workflow

    config = {
        "execution": {"auto_publish_threshold": 0.8, "rewrite_threshold": 0.5},
        "crawler_db": {
            "ready_to_publish_status": "ready_to_publish",
            "ready_to_rewrite_status": "ready_to_rewrite",
            "discard_status": "discarded",
        },
        "dedup": {"threshold": 0.8, "algorithm": "cosine"},
        "evaluation_criteria": {
            "min_quality_score": 0.5,
            "min_relevance_score": 0.4,
            "min_seo_potential_score": 0.4,
            "min_word_count": 80,
            "max_word_count": 5000,
            "short_content_threshold": 300,
            "short_content_bonus": 1.1,
        },
    }

    items = [
        {
            "id": 1,
            "title": "企业如何落地多 Agent 工作流：从选题到发布",
            "content": "本文讨论多 Agent 系统在内容生产中的落地路径，包括调度、编排、质量评估与发布对接。",
            "source_url": "https://example.com/a",
            "published_at": None,
            "author": None,
            "category": None,
            "spider_name": "demo",
        },
        {
            "id": 2,
            "title": "AI 资讯速报",
            "content": "AI 新品发布。AI 新品发布。AI 新品发布。",
            "source_url": "https://example.com/b",
            "published_at": None,
            "author": None,
            "category": None,
            "spider_name": "demo",
        },
    ]

    out = asyncio.run(
        run_crawler_workflow(
            items=items,
            target_keywords=[keyword] if keyword else [],
            dry_run=True,
            config=config,
        )
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))

def run_topic_hybrid(keyword: str, *, topic_limit: int = 5, topic_agent_mode: str | None = None):
    from workflows.topic_to_hybrid_adapter import run_topic_agent_then_hybrid

    out = run_topic_agent_then_hybrid(
        seed_keywords=[keyword] if keyword else [],
        topic_limit=topic_limit,
        topic_agent_mode=topic_agent_mode,
        config_dir="agents",
        image_mode="plan_only",
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="多Agent自动运营网站启动脚本")
    parser.add_argument("--engine", type=str, choices=["crewai", "langgraph", "hybrid", "crawler", "topic_hybrid"], default="hybrid", help="选择执行引擎 (默认: hybrid)")
    parser.add_argument("--topic", type=str, default="大语言模型在企业中的应用指南", help="文章选题标题")
    parser.add_argument("--keyword", type=str, default="企业级LLM应用", help="主关键词")
    parser.add_argument("--topic_limit", type=int, default=5, help="TopicAgent 生成选题数量（topic_hybrid 引擎）")
    parser.add_argument("--topic_agent_mode", type=str, default=None, help="TopicAgent 模式：mock/live（topic_hybrid 引擎）")
    
    args = parser.parse_args()
    
    if args.engine == "crewai":
        run_crewai_pipeline(args.topic, args.keyword)
    elif args.engine == "langgraph":
        run_langgraph_workflow(args.topic, args.keyword)
    elif args.engine == "hybrid":
        run_hybrid_workflow(args.topic, args.keyword)
    elif args.engine == "crawler":
        run_crawler_ingest(args.keyword)
    elif args.engine == "topic_hybrid":
        run_topic_hybrid(args.keyword, topic_limit=args.topic_limit, topic_agent_mode=args.topic_agent_mode)
