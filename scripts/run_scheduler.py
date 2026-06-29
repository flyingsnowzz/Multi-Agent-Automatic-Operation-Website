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

    while not shutdown:
        run_n += 1
        round_start = time.time()
        print(f"\n{'='*60}")
        print(f"🔄 第 {run_n} 轮 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")

        try:
            processed = await run_production(count=args.count, publish=args.publish, source=args.source)
            if processed == 0:
                empty_streak += 1
                current_interval = min(base_interval * (2 ** empty_streak), MAX_INTERVAL)
                print(f"📭 本轮无新文章 (连续 {empty_streak} 轮)，下次间隔 {current_interval}s")
            else:
                empty_streak = 0
                current_interval = base_interval
                print(f"✅ 本轮处理 {processed} 篇")
        except Exception as e:
            print(f"❌ 本轮异常: {e}")
            import traceback
            traceback.print_exc()
            current_interval = base_interval

        if shutdown:
            break

        elapsed = time.time() - round_start
        wait = max(0, current_interval - elapsed)
        print(f"\n⏳ 本轮耗时 {elapsed:.0f}s, 等待 {wait:.0f}s 后下一轮...")
        await asyncio.sleep(wait)

    print("👋 调度器已停止")


if __name__ == "__main__":
    asyncio.run(main())
