"""统一日志配置。

进程内只需调用一次 :func:`setup_logging`；其余模块通过 :func:`get_logger`
获取带命名空间的 logger。
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "log"
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-12s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(level: str | None = None, *, to_file: bool = True) -> None:
    """初始化根 logger（幂等）。"""
    global _configured
    if _configured:
        return

    resolved = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    root = logging.getLogger("proxypool")
    root.setLevel(resolved)
    root.propagate = False

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if to_file:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            # 单个文件最大 10MB，保留 5 个备份；按大小轮转，容器内多次重建也不会堆积。
            file_handler = RotatingFileHandler(
                LOG_DIR / "proxypool.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError:  # 只读文件系统等场景下降级为仅控制台输出
            root.warning("无法写入日志文件，已降级为仅控制台输出")

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """获取子 logger，自动补全 ``proxypool.`` 前缀。"""
    setup_logging()
    if not name.startswith("proxypool"):
        name = f"proxypool.{name}"
    return logging.getLogger(name)
