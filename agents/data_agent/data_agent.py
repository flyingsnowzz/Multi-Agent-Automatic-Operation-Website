import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import yaml

from agents.data_agent.tools.analytics_collector import AnalyticsCollector, DataSource
from agents.data_agent.tools.report_generator import ReportGenerator


def _deep_env_resolve(value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            key = value[2:-1]
            return os.environ.get(key, "")
        return value
    if isinstance(value, dict):
        return {k: _deep_env_resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_env_resolve(v) for v in value]
    return value


class DataAgent:
    def __init__(self, config_path: str = "agents/data_agent/config.yaml"):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_path):
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return _deep_env_resolve(raw)

    def _get_enabled_sources(self) -> Tuple[List[DataSource], List[str]]:
        cfg = self.config or {}
        sources_cfg = cfg.get("data_sources") or {}
        sources: List[DataSource] = []
        errors: List[str] = []

        def _is_enabled(name: str) -> bool:
            return bool((sources_cfg.get(name) or {}).get("enabled", False))

        if _is_enabled("google_analytics"):
            sources.append(DataSource.GOOGLE_ANALYTICS)
        if _is_enabled("google_search_console"):
            sources.append(DataSource.SEARCH_CONSOLE)
        if _is_enabled("baidu_tongji"):
            sources.append(DataSource.BAIDU_ANALYTICS)

        if _is_enabled("ahrefs"):
            errors.append("data_source_not_implemented:ahrefs")
        if _is_enabled("semrush"):
            errors.append("data_source_not_implemented:semrush")

        return sources, errors

    def _get_thresholds(self) -> Dict[str, float]:
        thresholds = (((self.config or {}).get("anomaly_detection") or {}).get("thresholds") or {})
        out: Dict[str, float] = {}
        for k, v in thresholds.items():
            try:
                out[str(k)] = float(v)
            except Exception:
                continue
        return out

    def _default_periods(self, report_type: str, today: Optional[date] = None) -> Tuple[Dict[str, str], Dict[str, str]]:
        today = today or date.today()
        if report_type == "daily":
            cur_start = today
            cur_end = today
            prev_start = today - timedelta(days=1)
            prev_end = prev_start
            return (
                {"start": cur_start.strftime("%Y-%m-%d"), "end": cur_end.strftime("%Y-%m-%d")},
                {"start": prev_start.strftime("%Y-%m-%d"), "end": prev_end.strftime("%Y-%m-%d")},
            )

        if report_type == "weekly":
            last_week_monday = today - timedelta(days=today.weekday() + 7)
            last_week_sunday = last_week_monday + timedelta(days=6)
            prev_week_monday = last_week_monday - timedelta(days=7)
            prev_week_sunday = prev_week_monday + timedelta(days=6)
            return (
                {"start": last_week_monday.strftime("%Y-%m-%d"), "end": last_week_sunday.strftime("%Y-%m-%d")},
                {"start": prev_week_monday.strftime("%Y-%m-%d"), "end": prev_week_sunday.strftime("%Y-%m-%d")},
            )

        if report_type == "monthly":
            first_this_month = today.replace(day=1)
            last_month_end = first_this_month - timedelta(days=1)
            last_month_start = last_month_end.replace(day=1)

            prev_month_end = last_month_start - timedelta(days=1)
            prev_month_start = prev_month_end.replace(day=1)
            return (
                {"start": last_month_start.strftime("%Y-%m-%d"), "end": last_month_end.strftime("%Y-%m-%d")},
                {"start": prev_month_start.strftime("%Y-%m-%d"), "end": prev_month_end.strftime("%Y-%m-%d")},
            )

        raise ValueError(f"unsupported_report_type:{report_type}")

    def _parse_number(self, v: Any) -> float:
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            s = v.strip().replace("%", "")
            try:
                return float(s)
            except Exception:
                return 0.0
        return 0.0

    def _compute_comparison_metrics(self, *, current: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
        metrics = ["sessions", "pageviews", "users", "bounce_rate"]
        out: Dict[str, Any] = {}
        for m in metrics:
            curr = self._parse_number(current.get(m))
            prev = self._parse_number(previous.get(m))
            if prev != 0:
                change = (curr - prev) / prev * 100.0
            else:
                change = 0.0
            out[m] = {
                "current": curr,
                "previous": prev,
                "change_percent": round(change, 2),
                "trend": "up" if change > 0 else ("down" if change < 0 else "flat"),
            }
        return out

    def _detect_anomalies(self, *, comparison_metrics: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        thresholds = self._get_thresholds()
        anomalies: List[Dict[str, Any]] = []
        notifications: List[Dict[str, Any]] = []

        traffic_drop_threshold = thresholds.get("traffic_drop_percent")
        if traffic_drop_threshold is not None:
            for metric in ("sessions", "pageviews"):
                item = comparison_metrics.get(metric) or {}
                change_percent = self._parse_number(item.get("change_percent"))
                if change_percent <= -float(traffic_drop_threshold):
                    severity = "high" if change_percent <= -float(traffic_drop_threshold) * 2 else "medium"
                    a = {
                        "type": "traffic_drop",
                        "severity": severity,
                        "metric": metric,
                        "current": item.get("current"),
                        "previous": item.get("previous"),
                        "change_percent": item.get("change_percent"),
                        "description": f"{metric} 下降 {abs(change_percent):.2f}%",
                        "suggestions": [
                            "检查近期内容发布/下线与内链变更",
                            "对比 Search Console 的点击/展示是否同步下滑",
                            "检查最近是否有技术故障（可用性、抓取、重定向、速度）",
                        ],
                    }
                    anomalies.append(a)
                    notifications.append({"title": "流量下降异常", "severity": severity, "payload": a})

        bounce_threshold = thresholds.get("bounce_rate_increase")
        if bounce_threshold is not None:
            item = comparison_metrics.get("bounce_rate") or {}
            change_percent = self._parse_number(item.get("change_percent"))
            if change_percent >= float(bounce_threshold):
                severity = "high" if change_percent >= float(bounce_threshold) * 2 else "medium"
                a = {
                    "type": "bounce_rate_increase",
                    "severity": severity,
                    "metric": "bounce_rate",
                    "current": item.get("current"),
                    "previous": item.get("previous"),
                    "change_percent": item.get("change_percent"),
                    "description": f"跳出率上升 {change_percent:.2f}%",
                    "suggestions": [
                        "检查落地页内容是否与搜索意图匹配（标题/首屏信息）",
                        "优化页面加载速度与移动端体验",
                        "增强内链与相关推荐，降低单页退出",
                    ],
                }
                anomalies.append(a)
                notifications.append({"title": "跳出率异常", "severity": severity, "payload": a})

        ranking_drop_positions = thresholds.get("ranking_drop_positions")
        if ranking_drop_positions is not None:
            anomalies.append(
                {
                    "type": "skipped",
                    "severity": "low",
                    "metric": "position",
                    "description": "ranking_drop_positions 依赖 GSC 数据，本次未实现 GSC 真实采集，已跳过检测",
                    "suggestions": ["后续接入 GSC Search Analytics API 后启用该异常检测"],
                }
            )

        return anomalies, notifications

    def _generate_recommendations(self, *, anomalies: List[Dict[str, Any]], comparison_metrics: Dict[str, Any]) -> List[str]:
        recs: List[str] = []
        if any(a.get("type") == "traffic_drop" for a in anomalies):
            recs.append("优先排查导致流量下滑的页面与来源渠道：对比 Top pages / Top sources 的变化并定位贡献最大的下滑项。")
        if any(a.get("type") == "bounce_rate_increase" for a in anomalies):
            recs.append("针对跳出率上升的 Top 页面做首屏与内链优化：补充目录、增加相关推荐、强化搜索意图匹配。")

        sessions_change = (comparison_metrics.get("sessions") or {}).get("change_percent")
        if sessions_change is not None and self._parse_number(sessions_change) > 10:
            recs.append("流量增长明显：复盘贡献最大的页面与渠道，把有效选题与分发策略沉淀为可复用模板。")

        return recs

    async def execute(
        self,
        report_type: str = "daily",
        date_range: Optional[Dict[str, str]] = None,
        sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        enabled_sources, source_errors = self._get_enabled_sources()
        errors: List[str] = list(source_errors)

        if sources is not None:
            chosen: List[DataSource] = []
            for s in sources:
                try:
                    chosen.append(DataSource(str(s)))
                except Exception:
                    errors.append(f"unknown_source:{s}")
            enabled_sources = chosen

        if date_range:
            current_period = {"start": date_range.get("start") or "", "end": date_range.get("end") or ""}
            if not current_period["start"] or not current_period["end"]:
                raise ValueError("date_range_requires_start_and_end")
            cur_start_dt = datetime.strptime(current_period["start"], "%Y-%m-%d").date()
            cur_end_dt = datetime.strptime(current_period["end"], "%Y-%m-%d").date()
            delta_days = (cur_end_dt - cur_start_dt).days + 1
            prev_end_dt = cur_start_dt - timedelta(days=1)
            prev_start_dt = prev_end_dt - timedelta(days=max(delta_days - 1, 0))
            previous_period = {"start": prev_start_dt.strftime("%Y-%m-%d"), "end": prev_end_dt.strftime("%Y-%m-%d")}
        else:
            current_period, previous_period = self._default_periods(report_type)

        collector = AnalyticsCollector()
        try:
            current_data = await collector.collect(
                sources=enabled_sources,
                start_date=current_period["start"],
                end_date=current_period["end"],
            )
            previous_data = await collector.collect(
                sources=enabled_sources,
                start_date=previous_period["start"],
                end_date=previous_period["end"],
            )
        finally:
            await collector.close()

        current_ga = (current_data.get("google_analytics") or {}).get("data") or {}
        previous_ga = (previous_data.get("google_analytics") or {}).get("data") or {}
        comparison_metrics = self._compute_comparison_metrics(current=current_ga, previous=previous_ga)
        anomalies, notifications = self._detect_anomalies(comparison_metrics=comparison_metrics)
        recommendations = self._generate_recommendations(anomalies=anomalies, comparison_metrics=comparison_metrics)

        generator = ReportGenerator()
        report = generator.generate_structured(
            report_type=report_type,
            current_period=current_period,
            previous_period=previous_period,
            current_data=current_data,
            previous_data=previous_data,
            comparison_metrics=comparison_metrics,
            anomalies=anomalies,
            recommendations=recommendations,
        )

        return {
            "success": True,
            "report_type": report_type,
            "period": {"current": current_period, "previous": previous_period},
            "sources": [s.value for s in enabled_sources],
            "data": {"current": current_data, "previous": previous_data},
            "comparison": {"metrics": comparison_metrics},
            "anomalies": anomalies,
            "recommendations": recommendations,
            "notifications": notifications,
            "report": report,
            "errors": errors,
            "timestamp": datetime.now().isoformat(),
        }

