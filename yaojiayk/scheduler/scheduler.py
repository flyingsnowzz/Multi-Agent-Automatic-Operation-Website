"""
多Agent自动运营网站 - 定时任务调度器
使用 APScheduler 实现精确的定时调度

使用方法:
    # 启动调度器
    scheduler = get_scheduler()
    scheduler.start()
    
    # 手动触发任务
    scheduler.trigger_job('daily_topic', auto_approve=True)  # 自主运营
    
    # 查看任务列表
    print(scheduler.scheduler.get_jobs())
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from datetime import datetime, timedelta
import asyncio
import logging
from typing import Optional, Dict, Any, List
from enum import Enum

from config.logging_config import setup_logging


logger = logging.getLogger(__name__)


class JobStatus(Enum):
    """任务状态枚举"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskRecord:
    """任务执行记录"""
    def __init__(self, job_id: str, status: JobStatus, result: Any = None, error: str = None):
        self.job_id = job_id
        self.status = status
        self.result = result
        self.error = error
        self.started_at = datetime.now()
        self.finished_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        return {
            'job_id': self.job_id,
            'status': self.status.value,
            'result': self.result,
            'error': self.error,
            'started_at': self.started_at.isoformat(),
            'finished_at': self.finished_at.isoformat() if self.finished_at else None
        }


class AgentScheduler:
    """Agent定时任务调度器
    
    支持的调度策略:
    - Cron表达式调度（APScheduler CronTrigger）
    - 间隔调度（每N分钟/小时）
    - 一次性调度（指定时间执行一次）
    - 事件触发调度（通过API触发）
    
    示例:
        scheduler = AgentScheduler()
        
        # 添加每日选题任务
        scheduler.add_daily_job(
            job_id='daily_topic',
            func=run_topic_agent,
            hour=8, minute=0,
            name='每日选题研究'
        )
        
        # 添加每周竞品分析
        scheduler.add_weekly_job(
            job_id='weekly_competitor',
            func=run_competitor_agent,
            day_of_week='mon', hour=9, minute=0,
            name='每周竞品分析'
        )
        
        scheduler.start()
    """
    
    def __init__(self, job_store: str = 'default'):
        """初始化调度器
        
        Args:
            job_store: 任务存储类型 ('default' 使用内存存储)
        """
        self.job_store = job_store
        self.scheduler = AsyncIOScheduler(
            jobstores={
                'default': MemoryJobStore()
            },
            executors={
                'default': AsyncIOExecutor()
            },
            job_defaults={
                'coalesce': False,  # 不合并错过的任务
                'max_instances': 1,  # 同一任务最多1个实例运行
                'misfire_grace_time': 60 * 60  # 1小时内错过的任务仍会执行
            }
        )
        self.task_history: List[TaskRecord] = []
        self._register_default_jobs()
    
    def _register_default_jobs(self):
        """注册默认的定时任务"""
        
        # === 每日任务 ===
        
        # 每日选题 - 每天早上8:00
        self.add_cron_job(
            job_id='daily_topic',
            func=self._run_topic_agent_wrapper,
            hour=8, minute=0,
            timezone='Asia/Shanghai',
            name='每日选题研究',
            kwargs={'auto_approve': True}  # 自主运营
        )
        
        # 每日数据采集 - 每天晚上20:00
        self.add_cron_job(
            job_id='daily_data_collection',
            func=self._run_data_agent_wrapper,
            hour=20, minute=0,
            timezone='Asia/Shanghai',
            name='每日数据采集',
            kwargs={'report_type': 'daily'}
        )

        self.add_interval_job(
            job_id='crawler_ingest',
            func=self._run_crawler_ingest_wrapper,
            minutes=30,
            name='爬虫内容处理',
            enabled=False,
            kwargs={'limit': 10, 'dry_run': True}
        )
        
        # === 每周任务 ===
        
        # 每周竞品分析 - 每周一早上9:00
        self.add_cron_job(
            job_id='weekly_competitor',
            func=self._run_competitor_agent_wrapper,
            day_of_week='mon', hour=9, minute=0,
            timezone='Asia/Shanghai',
            name='每周竞品分析'
        )
        
        # 每周运营报告 - 每周一早上10:00
        self.add_cron_job(
            job_id='weekly_report',
            func=self._run_data_agent_wrapper,
            day_of_week='mon', hour=10, minute=0,
            timezone='Asia/Shanghai',
            name='每周运营报告',
            kwargs={'report_type': 'weekly'}
        )
        
        # 技术SEO检查 - 每周三凌晨2:00
        self.add_cron_job(
            job_id='weekly_tech_seo',
            func=self._run_tech_seo_agent_wrapper,
            day_of_week='wed', hour=2, minute=0,
            timezone='Asia/Shanghai',
            name='每周技术SEO检查'
        )
        
        # === 每月任务 ===
        
        # 每月质量回顾 - 每月1号早上10:00
        self.add_cron_job(
            job_id='monthly_review',
            func=self._run_monthly_review_wrapper,
            day=1, hour=10, minute=0,
            timezone='Asia/Shanghai',
            name='每月质量回顾'
        )
        
        logger.info("✅ 默认定时任务注册完成")
    
    def add_cron_job(
        self,
        job_id: str,
        func,
        name: str = None,
        enabled: bool = True,
        replace_existing: bool = True,
        **cron_kwargs
    ):
        """添加Cron定时任务
        
        Args:
            job_id: 任务唯一ID
            func: 要执行的函数
            name: 任务名称（用于日志显示）
            enabled: 是否启用
            replace_existing: 如果任务已存在是否替换
            **cron_kwargs: CronTrigger参数
                - year: 年（可选）
                - month: 月（可选）
                - day: 日（可选）
                - day_of_week: 星期（可选，0-6 或 mon-sun）
                - hour: 小时（可选）
                - minute: 分钟（可选）
                - second: 秒（可选，默认0）
                - start_date: 开始日期
                - end_date: 结束日期
                - timezone: 时区
        """
        job_args = cron_kwargs.pop("args", None) or ()
        job_kwargs = cron_kwargs.pop("kwargs", None) or {}
        trigger = CronTrigger(**cron_kwargs)
        
        self.scheduler.add_job(
            func=func,
            trigger=trigger,
            id=job_id,
            name=name or job_id,
            replace_existing=replace_existing,
            enabled=enabled,
            args=job_args,
            kwargs=job_kwargs,
        )
        logger.info(f"📅 添加Cron任务: {job_id} ({name or job_id})")
    
    def add_interval_job(
        self,
        job_id: str,
        func,
        name: str = None,
        enabled: bool = True,
        minutes: int = None,
        hours: int = None,
        seconds: int = None,
        replace_existing: bool = True,
        **interval_kwargs
    ):
        """添加间隔定时任务
        
        Args:
            job_id: 任务唯一ID
            func: 要执行的函数
            name: 任务名称
            enabled: 是否启用
            minutes: 间隔分钟数
            hours: 间隔小时数
            seconds: 间隔秒数
            replace_existing: 如果任务已存在是否替换
        """
        from apscheduler.triggers.interval import IntervalTrigger
        
        job_args = interval_kwargs.pop("args", None) or ()
        job_kwargs = interval_kwargs.pop("kwargs", None) or {}

        trigger = IntervalTrigger(
            minutes=minutes or 0,
            hours=hours or 0,
            seconds=seconds or 0,
            **interval_kwargs
        )
        
        self.scheduler.add_job(
            func=func,
            trigger=trigger,
            id=job_id,
            name=name or job_id,
            replace_existing=replace_existing,
            enabled=enabled,
            args=job_args,
            kwargs=job_kwargs,
        )
        logger.info(f"⏱️ 添加间隔任务: {job_id} ({name or job_id})")
    
    def add_one_time_job(
        self,
        job_id: str,
        func,
        run_date: datetime,
        name: str = None,
        args: tuple = None,
        kwargs: dict = None,
        replace_existing: bool = True
    ):
        """添加一次性任务
        
        Args:
            job_id: 任务唯一ID
            func: 要执行的函数
            run_date: 执行时间
            name: 任务名称
            args: 位置参数
            kwargs: 关键字参数
            replace_existing: 如果任务已存在是否替换
        """
        trigger = DateTrigger(run_date=run_date)
        
        self.scheduler.add_job(
            func=func,
            trigger=trigger,
            id=job_id,
            name=name or job_id,
            args=args or (),
            kwargs=kwargs or {},
            replace_existing=replace_existing
        )
        logger.info(f"🎯 添加一次性任务: {job_id} ({name or job_id}), 执行时间: {run_date}")
    
    def remove_job(self, job_id: str):
        """移除任务"""
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"🗑️ 移除任务: {job_id}")
        except Exception as e:
            logger.error(f"❌ 移除任务失败: {job_id} - {e}")
    
    def pause_job(self, job_id: str):
        """暂停任务"""
        try:
            self.scheduler.pause_job(job_id)
            logger.info(f"⏸️ 暂停任务: {job_id}")
        except Exception as e:
            logger.error(f"❌ 暂停任务失败: {job_id} - {e}")
    
    def resume_job(self, job_id: str):
        """恢复任务"""
        try:
            self.scheduler.resume_job(job_id)
            logger.info(f"▶️ 恢复任务: {job_id}")
        except Exception as e:
            logger.error(f"❌ 恢复任务失败: {job_id} - {e}")
    
    def trigger_job(self, job_id: str, **kwargs):
        """手动触发任务
        
        Args:
            job_id: 任务ID
            **kwargs: 传递给任务的额外参数
        """
        job = self.scheduler.get_job(job_id)
        if job:
            # 创建新的一次性任务来立即执行
            self.add_one_time_job(
                job_id=f'{job_id}_manual_{datetime.now().strftime("%Y%m%d%H%M%S")}',
                func=job.func,
                run_date=datetime.now(),
                kwargs={**job.kwargs, **kwargs}
            )
            logger.info(f"🚀 手动触发任务: {job_id}")
        else:
            logger.error(f"❌ 任务不存在: {job_id}")
    
    def get_jobs(self) -> List[Dict]:
        """获取所有任务列表"""
        jobs = self.scheduler.get_jobs()
        return [
            {
                'id': job.id,
                'name': job.name,
                'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None,
                'pending': job.pending,
            }
            for job in jobs
        ]
    
    def get_task_history(self, job_id: str = None, limit: int = 100) -> List[Dict]:
        """获取任务执行历史
        
        Args:
            job_id: 过滤特定任务ID（可选）
            limit: 返回记录数限制
        """
        history = self.task_history
        if job_id:
            history = [r for r in history if r.job_id == job_id]
        return [r.to_dict() for r in history[-limit:]]
    
    # === Agent执行包装器 ===
    # 说明：
    # - APScheduler 的 AsyncIOScheduler 会在事件循环中运行这些 async wrapper
    # - wrapper 的职责是：记录任务状态(TaskRecord) → 调用对应工作流 → 捕获异常并通知 → 写入历史
    # - 实际业务逻辑放在 workflows/ 下（例如 workflows/crewai_workflow.py 的 run_*_workflow）
    
    async def _run_topic_agent_wrapper(self, **kwargs):
        """选题Agent执行包装器"""
        record = TaskRecord('daily_topic', JobStatus.RUNNING)
        try:
            # 约定：workflows/crewai_workflow.py 提供 run_topic_workflow 等异步便捷入口，
            # 便于调度器只关心“何时触发”，而不关心内部如何组织 Agent/Task。
            from yaojiayk.workflows.crewai_workflow import run_topic_workflow
            result = await run_topic_workflow(**kwargs)
            record.status = JobStatus.SUCCESS
            record.result = result
            logger.info(f"✅ 选题任务完成: {result}")
        except Exception as e:
            record.status = JobStatus.FAILED
            record.error = str(e)
            logger.error(f"❌ 选题任务失败: {e}")
            await self._notify_error("选题Agent失败", str(e))
        finally:
            record.finished_at = datetime.now()
            self.task_history.append(record)
    
    async def _run_data_agent_wrapper(self, **kwargs):
        """数据Agent执行包装器"""
        report_type = kwargs.get('report_type', 'daily')
        record = TaskRecord(f'data_{report_type}', JobStatus.RUNNING)
        try:
            from yaojiayk.workflows.crewai_workflow import run_data_workflow
            result = await run_data_workflow(**kwargs)
            record.status = JobStatus.SUCCESS
            record.result = result
            logger.info(f"✅ 数据采集任务完成: {result}")
        except Exception as e:
            record.status = JobStatus.FAILED
            record.error = str(e)
            logger.error(f"❌ 数据采集任务失败: {e}")
            await self._notify_error("数据Agent失败", str(e))
        finally:
            record.finished_at = datetime.now()
            self.task_history.append(record)
    
    async def _run_competitor_agent_wrapper(self, **kwargs):
        """竞品Agent执行包装器"""
        record = TaskRecord('weekly_competitor', JobStatus.RUNNING)
        try:
            from yaojiayk.workflows.crewai_workflow import run_competitor_workflow
            result = await run_competitor_workflow(**kwargs)
            record.status = JobStatus.SUCCESS
            record.result = result
            logger.info(f"✅ 竞品分析完成: {result}")
        except Exception as e:
            record.status = JobStatus.FAILED
            record.error = str(e)
            logger.error(f"❌ 竞品分析失败: {e}")
            await self._notify_error("竞品Agent失败", str(e))
        finally:
            record.finished_at = datetime.now()
            self.task_history.append(record)
    
    async def _run_tech_seo_agent_wrapper(self, **kwargs):
        """技术SEO Agent执行包装器"""
        record = TaskRecord('weekly_tech_seo', JobStatus.RUNNING)
        try:
            from yaojiayk.workflows.crewai_workflow import run_tech_seo_workflow
            result = await run_tech_seo_workflow(**kwargs)
            record.status = JobStatus.SUCCESS
            record.result = result
            logger.info(f"✅ 技术SEO检查完成: {result}")
        except Exception as e:
            record.status = JobStatus.FAILED
            record.error = str(e)
            logger.error(f"❌ 技术SEO检查失败: {e}")
            await self._notify_error("技术SEO Agent失败", str(e))
        finally:
            record.finished_at = datetime.now()
            self.task_history.append(record)
    
    async def _run_monthly_review_wrapper(self, **kwargs):
        """月度回顾执行包装器"""
        record = TaskRecord('monthly_review', JobStatus.RUNNING)
        try:
            from yaojiayk.workflows.crewai_workflow import run_monthly_review_workflow
            result = await run_monthly_review_workflow(**kwargs)
            record.status = JobStatus.SUCCESS
            record.result = result
            logger.info(f"✅ 月度回顾完成: {result}")
        except Exception as e:
            record.status = JobStatus.FAILED
            record.error = str(e)
            logger.error(f"❌ 月度回顾失败: {e}")
            await self._notify_error("月度回顾失败", str(e))
        finally:
            record.finished_at = datetime.now()
            self.task_history.append(record)

    async def _run_crawler_ingest_wrapper(self, **kwargs):
        record = TaskRecord('crawler_ingest', JobStatus.RUNNING)
        try:
            from yaojiayk.workflows.crawler_workflow import run_crawler_workflow
            result = await run_crawler_workflow(**kwargs)
            record.status = JobStatus.SUCCESS
            record.result = result
            logger.info(f"✅ 爬虫内容处理完成: {result.get('counts')}")
        except Exception as e:
            record.status = JobStatus.FAILED
            record.error = str(e)
            logger.error(f"❌ 爬虫内容处理失败: {e}")
            await self._notify_error("爬虫内容处理失败", str(e))
        finally:
            record.finished_at = datetime.now()
            self.task_history.append(record)
    
    async def _notify_error(self, title: str, message: str):
        """发送错误通知
        
        TODO: 实现通知逻辑（邮件/企微/钉钉等）
        """
        logger.warning(f"📢 通知: {title} - {message}")
        # await send_notification(title, message)
    
    def start(self):
        """启动调度器"""
        self.scheduler.start()
        logger.info("✅ 定时任务调度器已启动")
    
    def shutdown(self, wait: bool = True):
        """关闭调度器
        
        Args:
            wait: 是否等待正在执行的任务完成
        """
        self.scheduler.shutdown(wait=wait)
        logger.info("🛑 定时任务调度器已关闭")
    
    def __enter__(self):
        """上下文管理器入口"""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.shutdown()


# === 单例模式 ===
_scheduler: Optional[AgentScheduler] = None


def get_scheduler() -> AgentScheduler:
    """获取调度器单例
    
    Returns:
        AgentScheduler: 调度器实例
    """
    global _scheduler
    if _scheduler is None:
        _scheduler = AgentScheduler()
    return _scheduler


# === 直接运行入口 ===
if __name__ == '__main__':
    import sys
    import os
    
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    
    setup_logging()
    
    # 启动调度器
    scheduler = get_scheduler()
    scheduler.start()
    
    print("\n📋 定时任务列表:")
    for job in scheduler.get_jobs():
        print(f"  - {job['name']}: {job['next_run_time']}")
    
    print("\n⏰ 调度器运行中，按 Ctrl+C 退出...")
    
    try:
        # 保持主进程运行
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 正在关闭调度器...")
        scheduler.shutdown()
