#!/usr/bin/env python3
"""
报告生成工具 - DataAgent
生成日报、周报、月报
"""

import json
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta


class ReportGenerator:
    """报告生成工具"""
    
    def __init__(self):
        self.report_templates = {
            "daily": self._daily_template,
            "weekly": self._weekly_template,
            "monthly": self._monthly_template
        }
    
    def generate(
        self,
        report_type: str,
        data: Dict[str, Any],
        template: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        生成报告
        
        Args:
            report_type: 报告类型 daily/weekly/monthly
            data: 报告数据
            template: 自定义模板
            
        Returns:
            报告内容
        """
        if report_type not in self.report_templates:
            return {
                "success": False,
                "error": f"不支持的报告类型: {report_type}"
            }
        
        # 生成报告
        generator_func = self.report_templates[report_type]
        
        if template:
            report_content = self._generate_from_template(data, template)
        else:
            report_content = generator_func(data)
        
        return {
            "success": True,
            "report_type": report_type,
            "generated_at": datetime.now().isoformat(),
            "content": report_content,
            "summary": self._generate_summary(data, report_type)
        }
    
    def _daily_template(self, data: Dict) -> Dict[str, Any]:
        """日报模板"""
        return {
            "title": f"运营日报 - {datetime.now().strftime('%Y年%m月%d日')}",
            "sections": [
                {
                    "name": "核心指标",
                    "subsections": [
                        "今日数据总览",
                        "与昨日对比"
                    ]
                },
                {
                    "name": "流量分析",
                    "subsections": [
                        "流量来源分布",
                        "热门页面TOP10",
                        "用户行为路径"
                    ]
                },
                {
                    "name": "内容表现",
                    "subsections": [
                        "今日发布内容",
                        "表现最佳内容",
                        "需要关注的内容"
                    ]
                },
                {
                    "name": "SEO健康度",
                    "subsections": [
                        "搜索展示情况",
                        "关键词排名变化",
                        "技术SEO问题"
                    ]
                },
                {
                    "name": "待办事项",
                    "items": []
                }
            ],
            "metrics": [
                "访问量(PV/UV)",
                "平均停留时长",
                "跳出率",
                "页面浏览量",
                "新访客比例"
            ]
        }
    
    def _weekly_template(self, data: Dict) -> Dict[str, Any]:
        """周报模板"""
        return {
            "title": f"运营周报 - {datetime.now().strftime('%Y年第%W周')}",
            "period": self._get_week_range(),
            "sections": [
                {
                    "name": "本周数据总览",
                    "metrics": [
                        "总访问量",
                        "环比变化",
                        "用户增长",
                        "内容产出"
                    ]
                },
                {
                    "name": "流量趋势",
                    "charts": [
                        "日访问量趋势图",
                        "小时分布热力图",
                        "来源渠道对比"
                    ]
                },
                {
                    "name": "内容分析",
                    "metrics": [
                        "发布文章数",
                        "平均阅读量",
                        "互动率",
                        "Top3内容"
                    ]
                },
                {
                    "name": "SEO表现",
                    "metrics": [
                        "关键词数量",
                        "平均排名",
                        "搜索展示量",
                        "点击率"
                    ]
                },
                {
                    "name": "竞品动态",
                    "items": []
                },
                {
                    "name": "问题与优化",
                    "items": []
                },
                {
                    "name": "下周计划",
                    "items": []
                }
            ]
        }
    
    def _monthly_template(self, data: Dict) -> Dict[str, Any]:
        """月报模板"""
        return {
            "title": f"运营月报 - {datetime.now().strftime('%Y年%m月')}",
            "period": self._get_month_range(),
            "sections": [
                {
                    "name": "月度核心指标",
                    "metrics": [
                        "月访问量",
                        "月独立访客",
                        "平均日访问量",
                        "环比增长率",
                        "同比增长率"
                    ]
                },
                {
                    "name": "流量分析",
                    "charts": [
                        "月访问量趋势",
                        "流量来源构成",
                        "地域分布",
                        "设备分布"
                    ]
                },
                {
                    "name": "内容产出",
                    "metrics": [
                        "发布文章总数",
                        "平均字数",
                        "产出字数总计",
                        "栏目分布"
                    ]
                },
                {
                    "name": "内容表现",
                    "metrics": [
                        "Top10文章",
                        "低效内容",
                        "用户互动情况"
                    ]
                },
                {
                    "name": "SEO分析",
                    "metrics": [
                        "收录情况",
                        "关键词库增长",
                        "排名变化",
                        "搜索流量"
                    ]
                },
                {
                    "name": "目标完成情况",
                    "items": []
                },
                {
                    "name": "下月计划",
                    "items": []
                }
            ]
        }
    
    def _generate_from_template(self, data: Dict, template: str) -> Dict:
        """从自定义模板生成"""
        # 支持使用Markdown模板
        return {
            "content": template,
            "variables": list(data.keys())
        }
    
    def _generate_summary(self, data: Dict, report_type: str) -> Dict:
        """生成摘要"""
        summary = {
            "period": report_type,
            "generated": datetime.now().isoformat(),
            "highlights": [],
            "concerns": []
        }
        
        # 提取关键数据
        metrics = data.get("metrics", {})
        
        # 亮点
        for key, value in metrics.items():
            if isinstance(value, dict) and value.get("change_percent", 0) > 10:
                summary["highlights"].append({
                    "metric": key,
                    "value": value.get("current", 0),
                    "change": value.get("change_percent", 0)
                })
        
        # 问题
        for key, value in metrics.items():
            if isinstance(value, dict) and value.get("change_percent", 0) < -10:
                summary["concerns"].append({
                    "metric": key,
                    "value": value.get("current", 0),
                    "change": value.get("change_percent", 0)
                })
        
        return summary
    
    def _get_week_range(self) -> Dict[str, str]:
        """获取本周日期范围"""
        today = datetime.now()
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        
        return {
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d")
        }
    
    def _get_month_range(self) -> Dict[str, str]:
        """获取本月日期范围"""
        today = datetime.now()
        start = today.replace(day=1)
        
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = start.replace(month=start.month + 1, day=1) - timedelta(days=1)
        
        return {
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d")
        }

    def generate_structured(
        self,
        *,
        report_type: str,
        current_period: Dict[str, str],
        previous_period: Dict[str, str],
        current_data: Dict[str, Any],
        previous_data: Dict[str, Any],
        comparison_metrics: Dict[str, Any],
        anomalies: List[Dict[str, Any]],
        recommendations: List[str],
    ) -> Dict[str, Any]:
        ga_current = (current_data.get("google_analytics") or {}).get("data") or {}
        ga_previous = (previous_data.get("google_analytics") or {}).get("data") or {}

        errors: List[Dict[str, Any]] = []
        for src_key, payload in (current_data or {}).items():
            if not isinstance(payload, dict):
                continue
            if payload.get("success") is False:
                errors.append({"source": src_key, "error": payload.get("error") or "failed", "details": payload.get("details")})

        return {
            "report_type": report_type,
            "generated_at": datetime.now().isoformat(),
            "period": {"current": current_period, "previous": previous_period},
            "data_snapshot": {
                "google_analytics": ga_current,
                "google_analytics_previous": ga_previous,
            },
            "comparison": {"metrics": comparison_metrics},
            "top_pages": ga_current.get("top_pages") or [],
            "top_sources": ga_current.get("top_sources") or [],
            "anomalies": anomalies,
            "recommendations": recommendations,
            "errors": errors,
        }
    
    def format_markdown(self, report: Dict) -> str:
        """格式化报告为Markdown"""
        if "data_snapshot" in report and "comparison" in report:
            return self.format_markdown_structured(report)

        content = report.get("content", {})
        title = content.get("title", "运营报告")
        
        md = f"# {title}\n\n"
        md += f"**生成时间**: {report.get('generated_at', '')}\n\n"
        
        # 添加摘要
        summary = report.get("summary", {})
        if summary.get("highlights"):
            md += "## 亮点\n\n"
            for item in summary["highlights"]:
                md += f"- {item['metric']}: {item['value']} (↑{item['change']}%)\n"
            md += "\n"
        
        if summary.get("concerns"):
            md += "## 关注\n\n"
            for item in summary["concerns"]:
                md += f"- {item['metric']}: {item['value']} (↓{abs(item['change'])}%)\n"
            md += "\n"
        
        # 添加各部分
        sections = content.get("sections", [])
        for section in sections:
            md += f"## {section.get('name', '')}\n\n"
            
            for metric in section.get("metrics", []):
                md += f"- {metric}\n"
            
            for subsection in section.get("subsections", []):
                md += f"### {subsection}\n\n"
            
            for item in section.get("items", []):
                md += f"- {item}\n"
            
            md += "\n"
        
        return md

    def format_markdown_structured(self, report: Dict[str, Any]) -> str:
        report_type = str(report.get("report_type") or "report")
        period = report.get("period") or {}
        current_period = period.get("current") or {}
        previous_period = period.get("previous") or {}
        title = f"运营{report_type}报 ({current_period.get('start')} ~ {current_period.get('end')})"

        md = f"# {title}\n\n"
        md += f"**生成时间**: {report.get('generated_at', '')}\n\n"
        md += f"**对比周期**: {previous_period.get('start')} ~ {previous_period.get('end')}\n\n"

        metrics = ((report.get("comparison") or {}).get("metrics") or {}) if isinstance(report.get("comparison"), dict) else {}
        md += "## 核心指标\n\n"
        md += "| 指标 | 本周期 | 上周期 | 变化 |\n"
        md += "|---|---:|---:|---:|\n"
        for k, name in (
            ("sessions", "Sessions"),
            ("pageviews", "Pageviews"),
            ("users", "Users"),
            ("bounce_rate", "Bounce Rate"),
        ):
            item = metrics.get(k) or {}
            md += f"| {name} | {item.get('current', 0)} | {item.get('previous', 0)} | {item.get('change_percent', 0)}% |\n"
        md += "\n"

        top_pages = report.get("top_pages") or []
        if isinstance(top_pages, list) and top_pages:
            md += "## Top Pages\n\n"
            for p in top_pages:
                if not isinstance(p, dict):
                    continue
                md += f"- {p.get('path')}: {p.get('pageviews')}\n"
            md += "\n"

        top_sources = report.get("top_sources") or []
        if isinstance(top_sources, list) and top_sources:
            md += "## Top Sources\n\n"
            for s in top_sources:
                if not isinstance(s, dict):
                    continue
                md += f"- {s.get('source')}: {s.get('sessions')}\n"
            md += "\n"

        anomalies = report.get("anomalies") or []
        if isinstance(anomalies, list) and anomalies:
            md += "## 异常与建议\n\n"
            for a in anomalies:
                if not isinstance(a, dict):
                    continue
                md += f"- [{a.get('severity', 'low')}] {a.get('description')}\n"
                for sug in a.get("suggestions") or []:
                    md += f"  - {sug}\n"
            md += "\n"

        recs = report.get("recommendations") or []
        if isinstance(recs, list) and recs:
            md += "## 下周期建议\n\n"
            for r in recs:
                md += f"- {r}\n"
            md += "\n"

        errs = report.get("errors") or []
        if isinstance(errs, list) and errs:
            md += "## 数据源状态\n\n"
            for e in errs:
                if not isinstance(e, dict):
                    continue
                md += f"- {e.get('source')}: {e.get('error')}\n"
            md += "\n"

        return md
    
    def format_html(self, report: Dict) -> str:
        """格式化报告为HTML"""
        md_text = self.format_markdown(report)
        try:
            from markdown import markdown as md_to_html
        except Exception:
            return json.dumps({"success": False, "error": "markdown_library_missing"}, ensure_ascii=False, indent=2)

        html_body = md_to_html(md_text, extensions=["tables", "fenced_code"])
        title = "运营报告"
        if "data_snapshot" in report:
            title = f"运营{report.get('report_type') or ''}报"
        else:
            title = str((report.get("content") or {}).get("title") or "运营报告")

        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
        h1 {{ color: #333; }}
        h2 {{ color: #666; border-bottom: 1px solid #ddd; padding-bottom: 10px; }}
        ul {{ line-height: 1.8; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; }}
        th {{ background: #f6f6f6; }}
    </style>
</head>
<body>
{html_body}
</body>
</html>
"""


# CrewAI Tool 包装
def get_report_generator_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("report_generator")
    def report_generator_tool(
        report_type: str,
        data_json: str,
        format: str = "markdown"
    ) -> str:
        """
        生成运营报告。
        
        Args:
            report_type: 报告类型 daily/weekly/monthly
            data_json: 报告数据的JSON字符串
            format: 输出格式 markdown/html/json
            
        Returns:
            生成的报告内容
        """
        data = json.loads(data_json)
        
        generator = ReportGenerator()
        report = generator.generate(report_type, data)
        
        if format == "markdown":
            return generator.format_markdown(report)
        elif format == "html":
            return generator.format_html(report)
        else:
            return json.dumps(report, ensure_ascii=False, indent=2)
    
    return report_generator_tool


if __name__ == "__main__":
    # 测试
    generator = ReportGenerator()
    
    # 示例数据
    data = {
        "metrics": {
            "sessions": {"current": 10000, "previous": 9000, "change_percent": 11.1},
            "pageviews": {"current": 25000, "previous": 22000, "change_percent": 13.6},
            "users": {"current": 5000, "previous": 4800, "change_percent": 4.2},
            "bounce_rate": {"current": 45, "previous": 50, "change_percent": -10}
        },
        "top_content": [
            {"title": "文章A", "views": 1000},
            {"title": "文章B", "views": 800}
        ]
    }
    
    report = generator.generate("daily", data)
    print(generator.format_markdown(report))
