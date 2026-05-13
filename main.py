#!/usr/bin/env python3
"""
多Agent自动运营网站 - 主入口文件
"""

import os
import argparse
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

from workflows.langgraph_workflow import MultiAgentWorkflow
from workflows.crewai_workflow import MultiAgentContentPipeline

def run_crewai_pipeline(topic_title: str, keyword: str):
    """运行基于 CrewAI 的工作流"""
    print(f"启动 CrewAI 工作流，选题：{topic_title}")
    
    topic = {
        "title": topic_title,
        "primary_keyword": keyword,
        "secondary_keywords": [],
        "content_type": "guide",
        "min_word_count": 1500,
        "max_word_count": 3000
    }
    
    pipeline = MultiAgentContentPipeline(config_dir="agents")
    result = pipeline.run_pipeline(topic)
    print("CrewAI 工作流执行完毕。")

def run_langgraph_workflow(topic_title: str, keyword: str):
    """运行基于 LangGraph 的工作流"""
    print(f"启动 LangGraph 工作流，选题：{topic_title}")
    
    topic = {
        "title": topic_title,
        "primary_keyword": keyword,
        "secondary_keywords": [],
        "content_type": "guide",
        "min_word_count": 1500,
        "max_word_count": 3000
    }
    
    workflow = MultiAgentWorkflow(config_dir="agents")
    result = workflow.run_workflow(topic)
    print("LangGraph 工作流执行完毕。")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="多Agent自动运营网站启动脚本")
    parser.add_argument("--engine", type=str, choices=["crewai", "langgraph"], default="langgraph", help="选择执行引擎 (默认: langgraph)")
    parser.add_argument("--topic", type=str, default="大语言模型在企业中的应用指南", help="文章选题标题")
    parser.add_argument("--keyword", type=str, default="企业级LLM应用", help="主关键词")
    
    args = parser.parse_args()
    
    if args.engine == "crewai":
        run_crewai_pipeline(args.topic, args.keyword)
    elif args.engine == "langgraph":
        run_langgraph_workflow(args.topic, args.keyword)
