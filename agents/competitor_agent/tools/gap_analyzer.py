#!/usr/bin/env python3
"""
内容差距分析工具 - CompetitorAgent
分析自身与竞品的内容差距，发现机会
"""

import json
import re
from typing import Dict, List, Any, Optional, Set
from collections import Counter


class GapAnalyzer:
    """内容差距分析工具"""
    
    def __init__(self):
        # 关键词分类阈值
        self.keyword_threshold = 0.3  # 关键词重合度阈值
    
    def analyze(
        self,
        own_content: Dict[str, Any],
        competitor_contents: List[Dict[str, Any]],
        own_keywords: List[str],
        competitor_keywords: List[List[str]]
    ) -> Dict[str, Any]:
        """
        分析内容差距
        
        Args:
            own_content: 自身内容信息
            competitor_contents: 竞品内容列表
            own_keywords: 自身关键词列表
            competitor_keywords: 各竞品关键词列表
            
        Returns:
            差距分析结果
        """
        # 转换关键词为集合
        own_keyword_set = set(own_keywords)
        
        # 计算竞品关键词并集
        all_competitor_keywords = set()
        for keywords in competitor_keywords:
            all_competitor_keywords.update(keywords)
        
        # 1. 发现竞有我无的机会关键词
        gaps = own_keyword_set.symmetric_difference(all_competitor_keywords)
        mine_only = own_keyword_set - all_competitor_keywords
        theirs_only = all_competitor_keywords - own_keyword_set
        
        # 2. 计算关键词重合度
        overlap = own_keyword_set & all_competitor_keywords
        overlap_ratio = len(overlap) / max(len(own_keyword_set | all_competitor_keywords), 1)
        
        # 3. 分析内容主题覆盖
        own_topics = self._extract_topics(own_content.get("titles", []))
        competitor_topics = []
        for comp in competitor_contents:
            competitor_topics.extend(self._extract_topics(comp.get("titles", [])))
        
        topics_only_theirs = own_topics - set(competitor_topics)
        topics_only_ours = set(competitor_topics) - own_topics
        
        # 4. 分析内容深度
        depth_analysis = self._analyze_depth(own_content, competitor_contents)
        
        # 5. 生成机会列表
        opportunities = self._generate_opportunities(
            theirs_only,
            topics_only_ours,
            depth_analysis
        )
        
        return {
            "success": True,
            "keyword_analysis": {
                "own_count": len(own_keyword_set),
                "competitor_count": len(all_competitor_keywords),
                "overlap_count": len(overlap),
                "overlap_ratio": round(overlap_ratio, 2),
                "unique_to_own": list(mine_only),
                "unique_to_competitor": list(theirs_only),
                "opportunity_keywords": list(theirs_only)[:20]  # Top 20机会关键词
            },
            "topic_analysis": {
                "own_topics": list(own_topics),
                "competitor_topics": list(set(competitor_topics)),
                "topics_gap": list(topics_only_ours)
            },
            "depth_analysis": depth_analysis,
            "opportunities": opportunities,
            "recommendations": self._generate_recommendations(opportunities)
        }
    
    def _extract_topics(self, titles: List[str]) -> Set[str]:
        """从标题中提取主题"""
        topics = set()
        
        # 常见主题词
        topic_keywords = {
            "价格": ["价格", "收费", "费用", "学费", "报价"],
            "排名": ["排名", "榜单", "Top", "最好的"],
            "对比": ["对比", "区别", "哪个好", "比较"],
            "申请": ["申请", "报考", "报名", "条件"],
            "就业": ["就业", "职业", "薪资", "前景"],
            "师资": ["师资", "教授", "导师", "讲师"],
            "课程": ["课程", "内容", "模块", "体系"],
            "校友": ["校友", "人脉", "资源", "圈子"],
            "体验": ["体验", "感受", "分享", "经历"],
            "指南": ["指南", "攻略", "教程", "方法"]
        }
        
        for title in titles:
            for topic, keywords in topic_keywords.items():
                if any(kw in title for kw in keywords):
                    topics.add(topic)
        
        return topics
    
    def _analyze_depth(
        self,
        own_content: Dict,
        competitor_contents: List[Dict]
    ) -> Dict:
        """分析内容深度"""
        own_word_count = own_content.get("total_words", 0)
        own_articles = own_content.get("article_count", 0)
        
        avg_own = own_word_count / max(own_articles, 1)
        
        # 计算竞品平均
        competitor_avgs = []
        for comp in competitor_contents:
            wc = comp.get("total_words", 0)
            ac = comp.get("article_count", 0)
            if ac > 0:
                competitor_avgs.append(wc / ac)
        
        avg_competitor = sum(competitor_avgs) / max(len(competitor_avgs), 1) if competitor_avgs else 0
        
        # 内容类型覆盖
        own_types = set(own_content.get("content_types", []))
        competitor_types = set()
        for comp in competitor_contents:
            competitor_types.update(comp.get("content_types", []))
        
        return {
            "own_average_words_per_article": round(avg_own),
            "competitor_average": round(avg_competitor),
            "depth_gap": round(avg_competitor - avg_own),
            "own_content_types": list(own_types),
            "competitor_content_types": list(competitor_types),
            "missing_content_types": list(competitor_types - own_types)
        }
    
    def _generate_opportunities(
        self,
        missing_keywords: Set[str],
        missing_topics: Set[str],
        depth_analysis: Dict
    ) -> List[Dict]:
        """生成机会列表"""
        opportunities = []
        
        # 关键词机会
        for kw in list(missing_keywords)[:10]:
            opportunities.append({
                "type": "keyword",
                "title": f"覆盖关键词「{kw}」",
                "priority": "high",
                "reason": "竞品已覆盖但我们未覆盖",
                "suggestion": f"围绕「{kw}」撰写专题内容"
            })
        
        # 主题机会
        for topic in list(missing_topics)[:5]:
            opportunities.append({
                "type": "topic",
                "title": f"补充{topic}相关内容",
                "priority": "medium",
                "reason": "该主题在竞品中表现良好",
                "suggestion": f"从{topic}角度策划内容"
            })
        
        # 深度机会
        if depth_analysis.get("depth_gap", 0) > 500:
            opportunities.append({
                "type": "depth",
                "title": "增加内容深度",
                "priority": "medium",
                "reason": f"竞品平均比我们的内容长{depth_analysis['depth_gap']}字",
                "suggestion": "将现有文章扩展为深度内容"
            })
        
        # 内容类型机会
        for content_type in depth_analysis.get("missing_content_types", []):
            opportunities.append({
                "type": "content_type",
                "title": f"增加{content_type}类型内容",
                "priority": "low",
                "reason": "竞品使用该内容形式",
                "suggestion": f"尝试{content_type}形式的内容"
            })
        
        # 按优先级排序
        priority_order = {"high": 0, "medium": 1, "low": 2}
        opportunities.sort(key=lambda x: priority_order.get(x["priority"], 3))
        
        return opportunities
    
    def _generate_recommendations(self, opportunities: List[Dict]) -> List[str]:
        """生成建议"""
        recommendations = []
        
        # 高优先级机会
        high_priority = [o for o in opportunities if o["priority"] == "high"]
        if high_priority:
            recommendations.append(
                f"优先处理 {len(high_priority)} 个高优先级机会，涉及关键词："
                + "、".join([o["title"].split("「")[1].rstrip("」") for o in high_priority[:5]])
            )
        
        # 主题覆盖
        topic_opportunities = [o for o in opportunities if o["type"] == "topic"]
        if topic_opportunities:
            recommendations.append(
                f"建议补充 {len(topic_opportunities)} 个内容主题："
                + "、".join([o["title"].replace("补充", "").replace("相关内容", "") for o in topic_opportunities])
            )
        
        # 深度建议
        depth_opportunities = [o for o in opportunities if o["type"] == "depth"]
        if depth_opportunities:
            recommendations.append(
                "增加文章深度，平均每篇文章增加500-1000字"
            )
        
        return recommendations
    
    def analyze_serp_gaps(
        self,
        target_keywords: List[str],
        serp_results: Dict[str, List[Dict]]
    ) -> Dict[str, Any]:
        """
        分析SERP差距
        
        Args:
            target_keywords: 目标关键词
            serp_results: 各关键词的SERP结果
            
        Returns:
            SERP差距分析
        """
        gaps = []
        
        for keyword in target_keywords:
            results = serp_results.get(keyword, [])
            
            # 检查我们的排名
            our_rank = None
            for i, result in enumerate(results):
                if result.get("domain") == "our_domain":  # 需要替换为实际域名
                    our_rank = i + 1
                    break
            
            # 计算机会
            if our_rank is None:
                gaps.append({
                    "keyword": keyword,
                    "status": "missing",
                    "our_rank": None,
                    "opportunity": "high",
                    "suggestion": "创建针对该关键词的内容"
                })
            elif our_rank > 10:
                gaps.append({
                    "keyword": keyword,
                    "status": "low_ranking",
                    "our_rank": our_rank,
                    "opportunity": "medium",
                    "suggestion": "优化现有内容，提升排名"
                })
        
        # 统计
        missing_count = sum(1 for g in gaps if g["status"] == "missing")
        low_ranking_count = sum(1 for g in gaps if g["status"] == "low_ranking")
        
        return {
            "success": True,
            "total_keywords": len(target_keywords),
            "missing_count": missing_count,
            "low_ranking_count": low_ranking_count,
            "gaps": gaps,
            "priority_keywords": [g["keyword"] for g in gaps if g["opportunity"] == "high"]
        }
    
    def generate_content_plan(
        self,
        opportunities: List[Dict],
        keyword_cluster: Dict[str, List[str]]
    ) -> Dict[str, Any]:
        """
        根据差距生成内容计划
        
        Args:
            opportunities: 机会列表
            keyword_cluster: 关键词聚类
            
        Returns:
            内容计划
        """
        plan = []
        
        for opportunity in opportunities[:15]:
            if opportunity["type"] == "keyword":
                keyword = opportunity["title"].split("「")[1].rstrip("」")
                
                # 找到相关关键词簇
                related_keywords = []
                for cluster_keyword, cluster_words in keyword_cluster.items():
                    if keyword in cluster_words:
                        related_keywords = cluster_words
                        break
                
                plan.append({
                    "priority": opportunity["priority"],
                    "title": f"{keyword}完整指南",
                    "target_keyword": keyword,
                    "related_keywords": related_keywords,
                    "content_type": "指南/教程",
                    "estimated_words": 2500,
                    "reason": opportunity["reason"]
                })
            elif opportunity["type"] == "topic":
                topic = opportunity["title"].replace("补充", "").replace("相关内容", "")
                
                plan.append({
                    "priority": opportunity["priority"],
                    "title": f"{topic}专题",
                    "content_type": topic,
                    "estimated_words": 3000,
                    "reason": opportunity["reason"]
                })
        
        # 按优先级排序
        priority_order = {"high": 0, "medium": 1, "low": 2}
        plan.sort(key=lambda x: priority_order.get(x["priority"], 3))
        
        return {
            "success": True,
            "total_articles": len(plan),
            "high_priority_count": sum(1 for p in plan if p["priority"] == "high"),
            "total_estimated_words": sum(p["estimated_words"] for p in plan),
            "content_plan": plan
        }


# CrewAI Tool 包装
def get_gap_analyzer_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("gap_analyzer")
    def gap_analyzer_tool(
        action: str,
        own_content_json: str = "{}",
        competitor_contents_json: str = "[]",
        own_keywords: str = "",
        competitor_keywords: str = ""
    ) -> str:
        """
        分析自身与竞品的内容差距。
        
        Args:
            action: 操作类型 analyze/serp_gaps/content_plan
            own_content_json: 自身内容信息的JSON字符串
            competitor_contents_json: 竞品内容列表的JSON字符串
            own_keywords: 自身关键词，用逗号分隔
            competitor_keywords: 竞品关键词，每行一组逗号分隔
            
        Returns:
            JSON格式的差距分析结果
        """
        own_content = json.loads(own_content_json)
        competitor_contents = json.loads(competitor_contents_json)
        
        own_kw_list = [k.strip() for k in own_keywords.split(",") if k.strip()]
        comp_kw_lists = [
            [k.strip() for k in line.split(",") if k.strip()]
            for line in competitor_keywords.split("\n")
            if line.strip()
        ]
        
        analyzer = GapAnalyzer()
        
        if action == "analyze":
            return json.dumps(
                analyzer.analyze(
                    own_content,
                    competitor_contents,
                    own_kw_list,
                    comp_kw_lists
                ),
                ensure_ascii=False,
                indent=2
            )
        elif action == "serp_gaps":
            # 需要SERP数据
            return json.dumps({
                "success": False,
                "error": "需要提供SERP数据"
            }, ensure_ascii=False, indent=2)
        else:
            return json.dumps({
                "success": False,
                "error": f"未知操作: {action}"
            }, ensure_ascii=False, indent=2)
    
    return gap_analyzer_tool


if __name__ == "__main__":
    # 测试
    analyzer = GapAnalyzer()
    
    own_content = {
        "titles": ["EMBA项目介绍", "如何选择EMBA"],
        "total_words": 5000,
        "article_count": 2,
        "content_types": ["指南", "对比"]
    }
    
    competitor_contents = [
        {
            "titles": ["EMBA学费排名", "2024EMBA申请指南", "EMBA和MBA区别"],
            "total_words": 12000,
            "article_count": 3,
            "content_types": ["排名", "指南", "对比", "FAQ"]
        }
    ]
    
    own_keywords = ["EMBA", "选择", "项目", "商学院"]
    competitor_keywords = [["EMBA", "学费", "排名"], ["申请", "指南", "条件"]]
    
    result = analyzer.analyze(
        own_content,
        competitor_contents,
        own_keywords,
        competitor_keywords
    )
    
    print(json.dumps(result, ensure_ascii=False, indent=2))
