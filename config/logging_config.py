"""统一日志配置模块

使用 Python 标准库 logging，提供：
- 控制台输出（开发调试用，彩色级别前缀）
- 按大小轮转的普通文本日志（logs/app.log）

所有模块只需 import logging; logger = logging.getLogger(__name__)
项目入口调用一次 setup_logging() 即可全局生效。
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_LOG_DIR = Path(os.environ.get("LOG_DIR", "logs"))
_LOG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONSOLE_FMT = "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"
DEFAULT_CONSOLE_DATE = "%m-%d %H:%M:%S"
DEFAULT_FILE_FMT = "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"
DEFAULT_FILE_DATE = "%Y-%m-%d %H:%M:%S"


class _LevelColoredFormatter(logging.Formatter):
    """为控制台级别标签添加 ANSI 颜色。"""

    COLORS: dict[int, str] = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, "")
        if color:
            record.levelname = f"{color}{record.levelname:<5}{self.RESET}"
        return super().format(record)


def setup_logging(
    *,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    file_max_bytes: int = 10 * 1024 * 1024,
    file_backup_count: int = 5,
    log_dir: Optional[str | Path] = None,
) -> None:
    """初始化全局日志配置。应在入口尽早调用一次。"""
    directory = Path(log_dir or _LOG_DIR)
    directory.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    for h in root.handlers[:]:
        root.removeHandler(h)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(console_level)
    console.setFormatter(_LevelColoredFormatter(DEFAULT_CONSOLE_FMT, DEFAULT_CONSOLE_DATE))
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        str(directory / "app.log"),
        maxBytes=file_max_bytes,
        backupCount=file_backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(logging.Formatter(DEFAULT_FILE_FMT, DEFAULT_FILE_DATE))
    root.addHandler(file_handler)

    for lib in ("urllib3", "httpx", "httpcore", "openai", "apscheduler"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    root.info("Logging initialized | dir=%s console=%s file=%s",
              directory, logging.getLevelName(console_level), logging.getLevelName(file_level))


def get_logger(name: str) -> logging.Logger:
    """返回指定名称的 logger。"""
    return logging.getLogger(name)


if os.environ.get("LOG_SETUP_AUTO") == "1":
    setup_logging()
