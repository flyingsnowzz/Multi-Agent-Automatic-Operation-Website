#!/usr/bin/env python3
"""生产调度器：每小时自动运行一次生产 Pipeline。
用法:
  python3 scripts/run_scheduler.py                # 开发模式 (dump + dry_run)
  python3 scripts/run_scheduler.py --source db --publish  # 生产模式
Ctrl+C 优雅退出。
"""

import asyncio, time, sys, signal, argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from datetime import datetime

INTERVAL_SECONDS = 3600  # 1 小时


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["db", "dump"], default="dump")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--interval", type=int, default=INTERVAL_SECONDS, help="间隔秒数 (默认 3600)")
    args = parser.parse_args()

    from scripts.run_production import run_production

    shutdown = False

    def on_signal(sig, frame):
        nonlocal shutdown
        print(f"\n🛑 收到信号，当前轮结束后退出...")
        shutdown = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    run_n = 0
    empty_streak = 0
    base_interval = args.interval
    current_interval = base_interval
    MAX_INTERVAL = 86400  # 最长 24 小时

    print(f"⏰ 生产调度器启动 | 间隔 {args.interval}s | source={args.source} publish={args.publish}")
    COOLDOWN = 60  # 有文章时两轮之间冷却 60s

    while not shutdown:
        run_n += 1
        print(f"\n{'='*60}")
        print(f"🔄 第 {run_n} 轮 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")

        if processed > 0:
            empty_streak = 0
            print(f"✅ 本轮处理 {processed} 篇，{COOLDOWN}s 后继续")
            if shutdown: break
            time.sleep(COOLDOWN)
            continue  # 立即下一轮，不等 interval
        
        # 本轮无文章，进入退避
        empty_streak += 1
        current_interval = min(base_interval * (2 ** empty_streak), MAX_INTERVAL)
        print(f"📭 本轮无新文章 (连续 {empty_streak} 轮)，下次 {current_interval}s")

        if shutdown:
            break

        print(f"\n⏳ 下一轮 {datetime.now().strftime('%H:%M:%S')} +{current_interval}s")
        for _ in range(current_interval):
            if shutdown:
                break
            time.sleep(1)

    print("👋 调度器已停止")


if __name__ == "__main__":
    asyncio.run(main())
