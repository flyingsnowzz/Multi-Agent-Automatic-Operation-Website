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
"""

import os
import json
import yaml
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
    error: Optional[str]
    retry_count: int


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
    
    def __init__(self, config_dir: str = "agents"):
        """
        初始化工作流
        
        Args:
            config_dir: Agent配置目录
        """
        self.config_dir = config_dir
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0.4)
        self.workflow = None
        self.compiled_workflow = None
        
        # 加载所有Agent配置
        self.agent_configs = self._load_all_configs()
        
        # 构建工作流
        self._build_workflow()
    
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
        
        print("✓ LangGraph工作流构建完成")
    
    def _research_node(self, state: PipelineState) -> PipelineState:
        """调研节点"""
        print("\n" + "="*60)
        print("【阶段1/6】调研Agent执行中...")
        print("="*60)
        
        try:
            topic = state["topic"]
            
            # 读取prompt模板
            prompt_path = os.path.join(self.config_dir, "research_agent", "prompt.md")
            with open(prompt_path, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
            
            # 填充模板
            prompt = prompt_template.replace("{title}", topic.get("title", ""))
            prompt = prompt.replace("{primary_keyword}", topic.get("primary_keyword", ""))
            prompt = prompt.replace("{content_type}", topic.get("content_type", ""))
            
            # 调用LLM
            messages = [
                SystemMessage(content="你是专业调研员，擅长收集和组织资料。"),
                HumanMessage(content=prompt)
            ]
            
            response = self.llm.invoke(messages)
            
            # 解析结果
            research_result = json.loads(response.content)
            
            # 更新状态
            state["research_result"] = research_result
            state["current_stage"] = WorkflowStage.RESEARCH
            state["error"] = None
            
            print("✓ 调研完成")
            print(f"  收集到 {len(research_result.get('statistics', []))} 条数据")
            print(f"  收集到 {len(research_result.get('cases', []))} 个案例")
            
        except Exception as e:
            print(f"✗ 调研失败: {str(e)}")
            state["error"] = str(e)
            state["current_stage"] = WorkflowStage.ERROR
        
        return state
    
    def _write_node(self, state: PipelineState) -> PipelineState:
        """写作节点"""
        print("\n" + "="*60)
        print("【阶段2/6】写作Agent执行中...")
        print("="*60)
        
        try:
            topic = state["topic"]
            research_result = state["research_result"]
            
            # 读取prompt模板
            prompt_path = os.path.join(self.config_dir, "writer_agent", "prompt.md")
            with open(prompt_path, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
            
            # 填充模板
            prompt = prompt_template.replace("{title}", topic.get("title", ""))
            prompt = prompt.replace("{primary_keyword}", topic.get("primary_keyword", ""))
            prompt = prompt.replace("{content_type}", topic.get("content_type", ""))
            prompt = prompt.replace("{research_materials}", json.dumps(research_result, ensure_ascii=False))
            
            # 调用LLM
            messages = [
                SystemMessage(content="你是高级撰稿人，擅长撰写高质量文章。"),
                HumanMessage(content=prompt)
            ]
            
            response = self.llm.invoke(messages)
            
            # 解析结果
            write_result = json.loads(response.content)
            
            # 更新状态
            state["write_result"] = write_result
            state["current_stage"] = WorkflowStage.WRITE
            state["error"] = None
            
            print("✓ 写作完成")
            print(f"  文章字数: {write_result.get('statistics', {}).get('word_count', 'N/A')}")
            
        except Exception as e:
            print(f"✗ 写作失败: {str(e)}")
            state["error"] = str(e)
            state["current_stage"] = WorkflowStage.ERROR
        
        return state
    
    def _edit_node(self, state: PipelineState) -> PipelineState:
        """编辑节点"""
        print("\n" + "="*60)
        print("【阶段3/6】编辑Agent执行中...")
        print("="*60)
        
        try:
            write_result = state["write_result"]
            
            # 读取prompt模板
            prompt_path = os.path.join(self.config_dir, "editor_agent", "prompt.md")
            with open(prompt_path, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
            
            # 填充模板
            prompt = prompt_template.replace("{title}", write_result.get("article", {}).get("title", ""))
            prompt = prompt.replace("{content}", write_result.get("article", {}).get("content", ""))
            
            # 调用LLM
            messages = [
                SystemMessage(content="你是审校编辑，擅长审校和润色文章。"),
                HumanMessage(content=prompt)
            ]
            
            response = self.llm.invoke(messages)
            
            # 解析结果
            edit_result = json.loads(response.content)
            
            # 更新状态
            state["edit_result"] = edit_result
            state["current_stage"] = WorkflowStage.EDIT
            state["error"] = None
            
            print("✓ 编辑审校完成")
            print(f"  质量评分: {edit_result.get('quality_score', {}).get('overall', 'N/A')}")
            
        except Exception as e:
            print(f"✗ 编辑失败: {str(e)}")
            state["error"] = str(e)
            state["current_stage"] = WorkflowStage.ERROR
        
        return state
    
    def _seo_node(self, state: PipelineState) -> PipelineState:
        """SEO优化节点"""
        print("\n" + "="*60)
        print("【阶段4/6】SEO Agent执行中...")
        print("="*60)
        
        # 简化实现...
        print("✓ SEO优化完成（简化实现）")
        state["current_stage"] = WorkflowStage.SEO
        return state
    
    def _image_node(self, state: PipelineState) -> PipelineState:
        """图片处理节点"""
        print("\n" + "="*60)
        print("【阶段5/6】图片Agent执行中...")
        print("="*60)
        
        # 简化实现...
        print("✓ 图片处理完成（简化实现）")
        state["current_stage"] = WorkflowStage.IMAGE
        return state
    
    def _cms_node(self, state: PipelineState) -> PipelineState:
        """CMS发布节点"""
        print("\n" + "="*60)
        print("【阶段6/6】CMS Agent执行中...")
        print("="*60)
        
        # 简化实现...
        print("✓ CMS发布完成（简化实现）")
        state["current_stage"] = WorkflowStage.CMS
        return state
    
    def _evolve_node(self, state: PipelineState) -> PipelineState:
        """自演化节点 — 根据发布后的性能数据，生成下一轮优化关键词"""
        print("\n" + "="*60)
        print("🔄 Agent自演化分析中...")
        print("="*60)

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

            print("✓ 自演化分析完成")
            print(f"  性能数据: PV {perf.get('page_views', 0)}, 跳出率 {perf.get('bounce_rate', 'N/A')}")
            print(f"  演化关键词: {', '.join(evolved[:5]) if evolved else '暂无数据'}")

        except Exception as e:
            print(f"⚠ 自演化分析失败: {str(e)}，不影响发布结果")
            state["evolved_keywords"] = []
            state["performance_data"] = {}

        state["current_stage"] = WorkflowStage.EVOLVE
        return state

    def _fetch_performance_data(self, topic_id: str) -> Dict[str, Any]:
        """从数据库获取历史性能数据（模拟实现）"""
        # 实际实现：从 PostgreSQL 查询 analytics 表
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
        print("\n" + "="*60)
        print("✗ 工作流执行失败")
        print(f"错误: {state.get('error')}")
        print("="*60)
        
        return state
    

    
    def run_workflow(self, topic: Dict[str, Any]) -> Dict[str, Any]:
        """
        运行工作流
        
        Args:
            topic: 选题信息
            
        Returns:
            执行结果
        """
        print("\n" + "="*60)
        print("开始执行LangGraph多Agent内容生产工作流")
        print("="*60 + "\n")
        
        # 初始状态（人类通过brand_config和quality_threshold参与，Agent全自主执行）
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
            retry_count=0
        )
        
        # 执行工作流
        result = self.compiled_workflow.invoke(initial_state)
        
        print("\n" + "="*60)
        print("工作流执行完成")
        print("="*60 + "\n")
        
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
    
    print("\n执行结果：")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
