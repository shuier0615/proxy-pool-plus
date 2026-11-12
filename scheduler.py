"""定时任务调度。

抓取与巡检都以协程方式运行在同一个事件循环内，避免旧版「线程池 + 进程池」
混用导致的 Redis 连接与日志句柄竞争。
"""

from __future__ import annotations

import asyncio
import signal
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import get_settings
from logging_setup import get_logger, setup_logging
from service import ProxyPoolService
from utils.runtime import run

logger = get_logger("scheduler")


async def _serve() -> None:
    settings = get_settings()
    service = ProxyPoolService(settings)

    logger.info("执行启动抓取……")
    await service.fetch()

    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    scheduler.add_job(
        service.fetch,
        "interval",
        minutes=settings.fetch_interval,
        id="proxy_fetch",
        name="代理抓取",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )
    scheduler.add_job(
        service.check,
        "interval",
        minutes=settings.check_interval,
        id="proxy_check",
        name="代理巡检",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )
    scheduler.start()
    logger.info(
        "调度已启动：抓取每 %d 分钟，巡检每 %d 分钟，时区 %s",
        settings.fetch_interval,
        settings.check_interval,
        settings.timezone,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        loop.add_signal_handler(getattr(signal, sig_name), stop.set)

    try:
        await stop.wait()
    except KeyboardInterrupt:  # pragma: no cover - 交互式退出
        pass
    finally:
        scheduler.shutdown(wait=False)
        logger.info("调度已停止")


def run_scheduler() -> None:
    """阻塞运行调度器。"""
    setup_logging()
    try:
        run(_serve())
    except KeyboardInterrupt:  # pragma: no cover
        logger.info("已退出")
        sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    run_scheduler()
