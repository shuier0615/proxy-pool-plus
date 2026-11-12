"""事件循环运行辅助。

代理校验会主动探测大量失效地址，连接被对端 RST 时事件循环会把
``ConnectionResetError`` 抛到异常处理器，属于预期噪音而非真实故障，这里
统一忽略；其他异常仍走默认处理器，便于排查问题。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def _exception_handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    exc = context.get("exception")
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
        return
    loop.default_exception_handler(context)


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """阻塞运行协程，并安装降噪异常处理器。"""
    with asyncio.Runner() as runner:
        runner.get_loop().set_exception_handler(_exception_handler)
        return runner.run(coro)
