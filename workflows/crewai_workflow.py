#!/usr/bin/env python3
"""
CrewAI工作流实现 - 多Agent内容生产流水线
版本: v1.0
创建时间: 2026-05-13
"""

import os
import json
import yaml
from typing import Dict, List, Any, Optional
from datetime import datetime

# CrewAI 相关导入
from crewai import Agent, Task, Crew, Process
from crewai.tools import tool

# 自定义工具导入
from agents.topic_agent.tools.keyword_research import KeywordResearchTool
from agents.topic_agent.tools.trend_detection import TrendDetectionTool
from agents.topic_agent.tools.serp_analysis import SERPAnalysisTool


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
                    print(f"✓ 已加载 {agent_name} 配置")
            else:
                print(f"✗ 未找到 {agent_name} 配置文件: {config_path}")
    
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
                tools=[
                    KeywordResearchTool(),
                    TrendDetectionTool(),
                    SERPAnalysisTool()
                ],
                llm=self._get_llm(topic_config)
            )
            print("✓ 已创建 TopicAgent")
        
        # 2. 调研Agent
        if "research_agent" in self.agents:
            research_config = self.agents["research_agent"]["config"]
            
            self.research_agent = Agent(
                role='调研研究员',
                goal='为文章收集全面的背景资料和素材',
                backstory='你是一位专业的研究员，擅长快速收集和整理各类资料。',
                verbose=True,
                allow_delegation=False,
                llm=self._get_llm(research_config)
            )
            print("✓ 已创建 ResearchAgent")
        
        # 3. 写作Agent
        if "writer_agent" in self.agents:
            writer_config = self.agents["writer_agent"]["config"]
            
            self.writer_agent = Agent(
                role='高级撰稿人',
                goal='撰写高质量、SEO友好的原创文章',
                backstory='''你是一位经验丰富的内容创作者，擅长撰写专业、易懂且有实用价值的文章。''',
                verbose=True,
                allow_delegation=False,
                llm=self._get_llm(writer_config)
            )
            print("✓ 已创建 WriterAgent")
        
        # 4. 编辑Agent
        if "editor_agent" in self.agents:
            editor_config = self.agents["editor_agent"]["config"]
            
            self.editor_agent = Agent(
                role='审校编辑',
                goal='审校和润色文章，确保质量和品牌一致性',
                backstory='你是一位严谨的编辑，专注于提升文章质量和可读性。',
                verbose=True,
                allow_delegation=False,
                llm=self._get_llm(editor_config)
            )
            print("✓ 已创建 EditorAgent")
        
        # 5. SEO Agent
        if "seo_agent" in self.agents:
            seo_config = self.agents["seo_agent"]["config"]
            
            self.seo_agent = Agent(
                role='SEO优化专家',
                goal='优化文章搜索引擎可见性，提升搜索排名',
                backstory='你是一位SEO专家，精通搜索引擎优化策略和技术。',
                verbose=True,
                allow_delegation=False,
                llm=self._get_llm(seo_config)
            )
            print("✓ 已创建 SEOAgent")
        
        # 6. 图片Agent
        if "image_agent" in self.agents:
            image_config = self.agents["image_agent"]["config"]
            
            self.image_agent = Agent(
                role='配图设计师',
                goal='为文章生成或选择合适的配图',
                backstory='你是一位视觉设计师，擅长创作符合文章主题的配图。',
                verbose=True,
                allow_delegation=False,
                llm=self._get_llm(image_config)
            )
            print("✓ 已创建 ImageAgent")
        
        # 7. CMS Agent
        if "cms_agent" in self.agents:
            cms_config = self.agents["cms_agent"]["config"]
            
            self.cms_agent = Agent(
                role='CMS发布员',
                goal='将文章准确发布到CMS系统',
                backstory='你是一位技术熟练的CMS操作员，确保内容正确发布。',
                verbose=True,
                allow_delegation=False,
                llm=self._get_llm(cms_config)
            )
            print("✓ 已创建 CMSAgent")
    
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
            
            输出格式：结构化JSON
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
            
            输出格式：包含article, seo_analysis, internal_links的JSON
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
            
            输出：
            1. 审校后文章
            2. 质量评分（1-100）
            3. 问题清单
            4. 润色说明
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
            
            输出：
            1. 优化后文章
            2. SEO报告
            3. Meta标签
            4. Schema标记
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
            
            输出：
            1. 图片URL
            2. Alt文本
            3. 图片描述
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
            ''',
            agent=self.cms_agent,
            context=[seo_task, image_task],
            expected_output="包含发布状态和URL的JSON对象"
        )
        
        # 创建Crew
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
        print("\n" + "="*60)
        print("开始执行多Agent内容生产流水线")
        print("="*60 + "\n")
        
        # 创建流水线
        crew = self.create_content_pipeline(topic)
        
        # 执行
        result = crew.kickoff()
        
        print("\n" + "="*60)
        print("流水线执行完成")
        print("="*60 + "\n")
        
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
    
    print("\n执行结果：")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
