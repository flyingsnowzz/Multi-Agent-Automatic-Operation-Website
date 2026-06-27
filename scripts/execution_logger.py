"""轻量执行日志：每次流水线运行写入一行 JSONL，便于回溯排查。"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class ExecutionLogger:
    """流水线执行日志器，输出 JSONL 到 logs/ 目录。

    用法:
        logger = ExecutionLogger()
        logger.agent_call("quality", {"title": "..."}, {"score": 77.6})
        logger.agent_call("research", {"content_len": 2275}, {"prompt_len": 18187})
        logger.close()
    """

    def __init__(self, run_id: Optional[str] = None, log_dir: str = "logs"):
        self.run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._file: Any = None
        self._counts: Dict[str, int] = {}

    def _ensure_file(self) -> None:
        if self._file is None:
            self._file = open(self.log_dir / f"{self.run_id}.jsonl", "a", encoding="utf-8")

    def _write(self, entry: Dict[str, Any]) -> None:
        entry["run_id"] = self.run_id
        entry["ts"] = datetime.now().isoformat()
        self._ensure_file()
        self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._file.flush()

    # ── 阶段级日志 ──

    def phase_start(self, phase: str, **meta: Any) -> None:
        self._write({"type": "phase", "phase": phase, "status": "start", **meta})

    def phase_end(self, phase: str, **meta: Any) -> None:
        self._write({"type": "phase", "phase": phase, "status": "end", **meta})

    # ── Agent 调用日志 ──

    def agent_call(
        self,
        agent: str,
        input_info: Dict[str, Any],
        output_info: Dict[str, Any],
        *,
        error: Optional[str] = None,
        **extra: Any,
    ) -> None:
        entry = {
            "type": "agent",
            "agent": agent,
            "input": input_info,
            "output": output_info,
            "status": "error" if error else "ok",
        }
        if error:
            entry["error"] = error
        entry.update(extra)
        self._write(entry)
        self._counts[agent] = self._counts.get(agent, 0) + 1

    # ── 汇总 ──

    def summary(self) -> Dict[str, int]:
        return dict(self._counts)

    def close(self) -> None:
        if self._file:
            self._write({"type": "summary", "counts": self._counts})
            self._file.close()
            self._file = None

    def __enter__(self) -> "ExecutionLogger":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
