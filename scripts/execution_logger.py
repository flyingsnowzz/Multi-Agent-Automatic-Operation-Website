"""轻量执行日志：每次流水线运行写入一行 JSONL，便于回溯排查。"""

import json
import logging
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

    def __init__(self, run_id: Optional[str] = None, log_dir: str = "logs",
                 *, py_logger: Optional[logging.Logger] = None):
        """初始化执行日志器。

        Args:
            run_id: 运行标识，默认按时间戳生成
            log_dir: 日志目录
            py_logger: 可选的 Python logger，用于同步输出到标准 logging 体系
        """
        self.run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._file: Any = None
        self._counts: Dict[str, int] = {}
        self._py_logger = py_logger or logging.getLogger("execution")

    def _ensure_file(self) -> None:
        if self._file is None:
            self._file = open(self.log_dir / f"{self.run_id}.jsonl", "a", encoding="utf-8")

    def _write(self, entry: Dict[str, Any]) -> None:
        entry["run_id"] = self.run_id
        entry["ts"] = datetime.now().isoformat()
        self._ensure_file()
        self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._file.flush()

    def _emit_py_log(self, entry: Dict[str, Any]) -> None:
        """同步写入 Python logging 体系。"""
        status = entry.get("status", "ok")
        etype = entry.get("type", "")
        if etype == "phase":
            self._py_logger.info(
                "%s phase=%s status=%s%s",
                "[JSONL]", entry.get("phase", ""), status,
                f" {entry.get('error', '')}" if status == "error" else "")
        elif etype == "agent":
            agent = entry.get("agent", "")
            inp_keys = list(entry.get("input", {}).keys())
            out_keys = list(entry.get("output", {}).keys())
            msg = f"[JSONL] agent={agent} status={status} input={inp_keys} output={out_keys}"
            if status == "error":
                msg += f" error={entry.get('error', '')}"
            self._py_logger.info(msg)

    # ── 阶段级日志 ──

    def phase_start(self, phase: str, **meta: Any) -> None:
        entry = {"type": "phase", "phase": phase, "status": "start", **meta}
        self._write(entry)
        self._emit_py_log(entry)

    def phase_end(self, phase: str, **meta: Any) -> None:
        entry = {"type": "phase", "phase": phase, "status": "end", **meta}
        self._write(entry)
        self._emit_py_log(entry)

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
        self._emit_py_log(entry)
        self._counts[agent] = self._counts.get(agent, 0) + 1

    # ── 汇总 ──

    def summary(self) -> Dict[str, int]:
        return dict(self._counts)

    def close(self) -> None:
        if self._file:
            s = {"type": "summary", "counts": self._counts}
            self._write(s)
            self._emit_py_log(s)
            self._py_logger.info("[JSONL] 执行完毕 run_id=%s counts=%s", self.run_id, self._counts)
            self._file.close()
            self._file = None

    def __enter__(self) -> "ExecutionLogger":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
