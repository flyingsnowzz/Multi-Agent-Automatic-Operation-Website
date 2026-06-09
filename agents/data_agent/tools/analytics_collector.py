#!/usr/bin/env python3
"""
数据分析收集工具 - DataAgent
从各种数据源收集网站运营数据
"""

import os
import json
import asyncio
import httpx
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from enum import Enum


class DataSource(str, Enum):
    """数据源枚举"""
    GOOGLE_ANALYTICS = "ga"
    BAIDU_ANALYTICS = "baidu"
    SEARCH_CONSOLE = "gsc"
    GOOGLE_TRENDS = "trends"
    BAIDU_INDEX = "baidu_index"


class AnalyticsCollector:
    """数据分析收集工具"""
    
    def __init__(self):
        self.http_client = httpx.AsyncClient(timeout=30.0)
        
        # API配置
        self.ga_property_id = os.environ.get("GA_PROPERTY_ID", "")
        self.google_application_credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "") or os.environ.get(
            "GA_CREDENTIALS", ""
        )
        self.baidu_token = os.environ.get("BAIDU_TOKEN", "")
        self.gsc_credentials = os.environ.get("GSC_CREDENTIALS", "") or self.google_application_credentials
    
    async def collect(
        self,
        sources: List[DataSource],
        start_date: str,
        end_date: str,
        dimensions: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        从多个数据源收集数据
        
        Args:
            sources: 数据源列表
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD
            dimensions: 维度配置
            
        Returns:
            收集的数据
        """
        results = {}
        
        for source in sources:
            if source == DataSource.GOOGLE_ANALYTICS:
                results["google_analytics"] = await self._collect_ga(start_date, end_date)
            elif source == DataSource.BAIDU_ANALYTICS:
                results["baidu_analytics"] = await self._collect_baidu(start_date, end_date)
            elif source == DataSource.SEARCH_CONSOLE:
                results["search_console"] = await self._collect_gsc(start_date, end_date)
            elif source == DataSource.BAIDU_INDEX:
                keyword = ""
                if isinstance(dimensions, dict):
                    keyword = str(dimensions.get("keyword") or "")
                results["baidu_index"] = await self._collect_baidu_index(start_date, end_date, keyword=keyword)
        
        return results

    async def _get_google_access_token(self) -> Dict[str, Any]:
        if not self.google_application_credentials:
            return {"success": False, "error": "missing_credentials"}

        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request
        except Exception:
            return {"success": False, "error": "google_auth_missing"}

        path = self.google_application_credentials

        def _refresh() -> str:
            creds = service_account.Credentials.from_service_account_file(
                path, scopes=["https://www.googleapis.com/auth/analytics.readonly"]
            )
            creds.refresh(Request())
            return str(creds.token or "")

        try:
            token = await asyncio.to_thread(_refresh)
        except Exception as e:
            return {"success": False, "error": f"google_auth_refresh_failed:{str(e)}"}

        if not token:
            return {"success": False, "error": "google_auth_token_empty"}
        return {"success": True, "token": token}

    async def _run_ga_report(
        self,
        *,
        token: str,
        start_date: str,
        end_date: str,
        metrics: List[str],
        dimensions: Optional[List[str]] = None,
        limit: int = 10,
        order_by_metric: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.ga_property_id:
            return {"success": False, "error": "ga_property_id_missing"}

        url = f"https://analyticsdata.googleapis.com/v1beta/properties/{self.ga_property_id}:runReport"

        body: Dict[str, Any] = {
            "dateRanges": [{"startDate": start_date, "endDate": end_date}],
            "metrics": [{"name": m} for m in metrics],
            "limit": str(limit),
        }
        if dimensions:
            body["dimensions"] = [{"name": d} for d in dimensions]
        if order_by_metric:
            body["orderBys"] = [{"metric": {"metricName": order_by_metric}, "desc": True}]

        try:
            resp = await self.http_client.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body,
            )
        except Exception as e:
            return {"success": False, "error": f"ga_request_failed:{str(e)}"}

        if resp.status_code >= 400:
            try:
                err = resp.json()
            except Exception:
                err = resp.text
            return {"success": False, "error": "ga_api_error", "status_code": resp.status_code, "details": err}

        try:
            data = resp.json()
        except Exception as e:
            return {"success": False, "error": f"ga_response_parse_failed:{str(e)}"}

        return {"success": True, "data": data}
    
    async def _collect_ga(
        self,
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        """收集Google Analytics数据"""
        if not self.google_application_credentials:
            return {
                "success": False,
                "error": "missing_credentials",
                "data": {}
            }

        token_res = await self._get_google_access_token()
        if not token_res.get("success"):
            return {"success": False, "error": token_res.get("error") or "token_error", "data": {}}
        token = token_res.get("token") or ""

        warnings: List[str] = []

        core_metrics_primary = ["sessions", "screenPageViews", "totalUsers", "bounceRate", "averageSessionDuration"]
        core_metrics_fallback = ["sessions", "screenPageViews", "totalUsers", "bounceRate", "averageEngagementTime"]
        core_report = await self._run_ga_report(
            token=token,
            start_date=start_date,
            end_date=end_date,
            metrics=core_metrics_primary,
            limit=1,
        )
        if not core_report.get("success"):
            warnings.append("averageSessionDuration_unavailable_fallback_to_averageEngagementTime")
            core_report = await self._run_ga_report(
                token=token,
                start_date=start_date,
                end_date=end_date,
                metrics=core_metrics_fallback,
                limit=1,
            )

        if not core_report.get("success"):
            return {
                "success": False,
                "error": core_report.get("error") or "ga_core_report_failed",
                "details": core_report.get("details"),
                "data": {},
            }

        raw = core_report.get("data") or {}
        rows = raw.get("rows") or []
        metric_vals = []
        if rows and isinstance(rows, list) and isinstance(rows[0], dict):
            metric_vals = (rows[0].get("metricValues") or []) if isinstance(rows[0].get("metricValues"), list) else []

        def _metric_at(idx: int) -> float:
            if idx < 0 or idx >= len(metric_vals):
                return 0.0
            mv = metric_vals[idx]
            if not isinstance(mv, dict):
                return 0.0
            v = mv.get("value")
            try:
                return float(v) if v is not None else 0.0
            except Exception:
                return 0.0

        sessions = _metric_at(0)
        pageviews = _metric_at(1)
        users = _metric_at(2)
        bounce_rate = _metric_at(3)
        avg_duration = _metric_at(4) if len(metric_vals) >= 5 else 0.0

        top_pages_report = await self._run_ga_report(
            token=token,
            start_date=start_date,
            end_date=end_date,
            metrics=["screenPageViews"],
            dimensions=["pagePath"],
            limit=10,
            order_by_metric="screenPageViews",
        )
        top_pages: List[Dict[str, Any]] = []
        if top_pages_report.get("success"):
            for r in (top_pages_report.get("data") or {}).get("rows") or []:
                if not isinstance(r, dict):
                    continue
                dims = r.get("dimensionValues") or []
                mvs = r.get("metricValues") or []
                path = ""
                if dims and isinstance(dims, list) and isinstance(dims[0], dict):
                    path = str(dims[0].get("value") or "")
                views = 0.0
                if mvs and isinstance(mvs, list) and isinstance(mvs[0], dict):
                    try:
                        views = float(mvs[0].get("value") or 0.0)
                    except Exception:
                        views = 0.0
                if path:
                    top_pages.append({"path": path, "pageviews": views})
        else:
            warnings.append("top_pages_report_failed")

        top_sources_report = await self._run_ga_report(
            token=token,
            start_date=start_date,
            end_date=end_date,
            metrics=["sessions"],
            dimensions=["sessionSource"],
            limit=10,
            order_by_metric="sessions",
        )
        top_sources: List[Dict[str, Any]] = []
        if top_sources_report.get("success"):
            for r in (top_sources_report.get("data") or {}).get("rows") or []:
                if not isinstance(r, dict):
                    continue
                dims = r.get("dimensionValues") or []
                mvs = r.get("metricValues") or []
                source = ""
                if dims and isinstance(dims, list) and isinstance(dims[0], dict):
                    source = str(dims[0].get("value") or "")
                sess = 0.0
                if mvs and isinstance(mvs, list) and isinstance(mvs[0], dict):
                    try:
                        sess = float(mvs[0].get("value") or 0.0)
                    except Exception:
                        sess = 0.0
                if source:
                    top_sources.append({"source": source, "sessions": sess})
        else:
            warnings.append("top_sources_report_failed")

        out = {
            "success": True,
            "data": {
                "date_range": f"{start_date} to {end_date}",
                "sessions": sessions,
                "pageviews": pageviews,
                "users": users,
                "bounce_rate": bounce_rate,
                "avg_session_duration": avg_duration,
                "top_pages": top_pages,
                "top_sources": top_sources,
            },
        }
        if warnings:
            out["warnings"] = warnings
        return out
    
    async def _collect_baidu(
        self,
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        """收集百度统计数据"""
        if not self.baidu_token:
            return {
                "success": False,
                "error": "missing_credentials",
                "data": {}
            }
        return {
            "success": False,
            "error": "not_implemented",
            "data": {"date_range": f"{start_date} to {end_date}"},
        }
    
    async def _collect_gsc(
        self,
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        """收集Google Search Console数据"""
        if not self.gsc_credentials:
            return {
                "success": False,
                "error": "missing_credentials",
                "data": {}
            }
        return {
            "success": False,
            "error": "not_implemented",
            "data": {"date_range": f"{start_date} to {end_date}"},
        }
    
    async def _collect_baidu_index(
        self,
        start_date: str,
        end_date: str,
        keyword: str = ""
    ) -> Dict[str, Any]:
        """收集百度指数数据"""
        if not keyword:
            return {"success": False, "error": "keyword_required", "data": {"date_range": f"{start_date} to {end_date}"}}
        return {"success": False, "error": "not_implemented", "data": {"keyword": keyword, "date_range": f"{start_date} to {end_date}"}}
    
    async def get_realtime_data(self) -> Dict[str, Any]:
        """获取实时数据"""
        return {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "active_users": 0,
            "pageviews_last_hour": 0,
            "top_active_pages": []
        }
    
    async def get_trends(
        self,
        keywords: List[str],
        timeframe: str = "today 3-m"
    ) -> Dict[str, Any]:
        """
        获取搜索趋势
        
        Args:
            keywords: 关键词列表
            timeframe: 时间范围
        """
        trends = {}
        
        for keyword in keywords:
            trends[keyword] = {
                "timeline": {},
                "interest_by_region": {},
                "related_queries": []
            }
        
        return {
            "success": True,
            "keywords": trends,
            "timeframe": timeframe
        }
    
    async def compare_periods(
        self,
        current_start: str,
        current_end: str,
        previous_start: str,
        previous_end: str,
        sources: Optional[List[DataSource]] = None,
    ) -> Dict[str, Any]:
        """对比两个时间段的数据"""
        sources = sources or [DataSource.GOOGLE_ANALYTICS]
        # 获取当前时间段数据
        current = await self.collect(
            sources,
            current_start,
            current_end
        )
        
        # 获取之前时间段数据
        previous = await self.collect(
            sources,
            previous_start,
            previous_end
        )
        
        # 计算变化
        current_ga = current.get("google_analytics", {}).get("data", {})
        previous_ga = previous.get("google_analytics", {}).get("data", {})
        
        comparison = {
            "periods": {
                "current": f"{current_start} to {current_end}",
                "previous": f"{previous_start} to {previous_end}"
            },
            "metrics": {}
        }
        
        # 计算各指标变化
        metrics_to_compare = ["sessions", "pageviews", "users", "bounce_rate"]
        
        for metric in metrics_to_compare:
            curr_val = current_ga.get(metric, 0)
            prev_val = previous_ga.get(metric, 0)
            
            if prev_val > 0:
                change = ((curr_val - prev_val) / prev_val) * 100
            else:
                change = 0
            
            comparison["metrics"][metric] = {
                "current": curr_val,
                "previous": prev_val,
                "change_percent": round(change, 2),
                "trend": "up" if change > 0 else ("down" if change < 0 else "flat")
            }
        
        return comparison
    
    async def detect_anomalies(
        self,
        days: int = 30,
        threshold: float = 2.0
    ) -> Dict[str, Any]:
        """
        异常检测
        
        Args:
            days: 分析天数
            threshold: 标准差阈值
            
        Returns:
            异常数据
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # 收集数据
        data = await self.collect(
            [DataSource.GOOGLE_ANALYTICS],
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d")
        )
        
        # 简化异常检测
        # 实际实现需要计算均值和标准差
        
        return {
            "success": True,
            "anomalies": [],
            "period_analyzed": days,
            "threshold": threshold,
            "note": "需要配置GA数据以进行实际异常检测"
        }
    
    async def close(self):
        """关闭客户端"""
        await self.http_client.aclose()


# CrewAI Tool 包装
def get_analytics_collector_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("analytics_collector")
    def analytics_collector_tool(
        action: str,
        sources: str = "ga",
        start_date: str = "",
        end_date: str = "",
        keywords: str = "",
        previous_start_date: str = "",
        previous_end_date: str = "",
    ) -> str:
        """
        收集网站分析数据。
        
        Args:
            action: 操作类型 collect/trends/anomalies/compare
            sources: 数据源，用逗号分隔，可选 ga/baidu/gsc/baidu_index
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD
            keywords: 关键词列表，用逗号分隔
            
        Returns:
            JSON格式的数据
        """
        import asyncio
        
        collector = AnalyticsCollector()
        
        async def run():
            if action == "collect":
                source_list = [DataSource(s.strip()) for s in sources.split(",")]
                return await collector.collect(
                    sources=source_list,
                    start_date=start_date,
                    end_date=end_date
                )
            elif action == "trends":
                keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
                return await collector.get_trends(keyword_list)
            elif action == "anomalies":
                return await collector.detect_anomalies()
            elif action == "realtime":
                return await collector.get_realtime_data()
            elif action == "compare":
                try:
                    cur_start = datetime.strptime(start_date, "%Y-%m-%d").date()
                    cur_end = datetime.strptime(end_date, "%Y-%m-%d").date()
                except Exception:
                    return {"success": False, "error": "compare_requires_start_date_and_end_date"}

                if previous_start_date and previous_end_date:
                    prev_start = previous_start_date
                    prev_end = previous_end_date
                else:
                    span = (cur_end - cur_start).days + 1
                    prev_end_dt = cur_start - timedelta(days=1)
                    prev_start_dt = prev_end_dt - timedelta(days=max(span - 1, 0))
                    prev_start = prev_start_dt.strftime("%Y-%m-%d")
                    prev_end = prev_end_dt.strftime("%Y-%m-%d")

                source_list = [DataSource(s.strip()) for s in sources.split(",") if s.strip()]
                return await collector.compare_periods(
                    current_start=start_date,
                    current_end=end_date,
                    previous_start=prev_start,
                    previous_end=prev_end,
                    sources=source_list or [DataSource.GOOGLE_ANALYTICS],
                )
            else:
                return {"success": False, "error": f"未知操作: {action}"}
        
        try:
            result = asyncio.run(run())
            return json.dumps(result, ensure_ascii=False, indent=2)
        finally:
            asyncio.run(collector.close())
    
    return analytics_collector_tool


if __name__ == "__main__":
    # 测试
    import asyncio
    
    async def test():
        collector = AnalyticsCollector()
        
        result = await collector.collect(
            [DataSource.GOOGLE_ANALYTICS],
            "2024-01-01",
            "2024-01-31"
        )
        
        print(json.dumps(result, ensure_ascii=False, indent=2))
        
        await collector.close()
    
    asyncio.run(test())
