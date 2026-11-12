"""代理可用性校验（异步）。

对每个代理依次（并发地）探测 HTTP / HTTPS 目标站点，只有真正能转发请求的
代理才会入池。探测使用 HEAD，失败时降级为流式 GET 只读首字节，兼顾速度与
兼容性。

HTTP 代理走 aiohttp 原生 ``proxy=`` 参数；SOCKS5 代理走 ``aiohttp-socks`` 的
``ProxyConnector``，每个代理一个短生命周期会话（SOCKS 通道绑定在连接器上，
无法像 HTTP 代理那样按请求切换）。实测 SOCKS5 仅完成握手不代表能转发数据，
因此同样必须用真实 HTTP 请求判定。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

import aiohttp
from aiohttp import ClientTimeout

from config import Settings, get_settings
from logging_setup import get_logger
from models import Proxy
from utils.net import build_session
from utils.proxy_parse import parse_proxy

try:  # 可选依赖：未安装时 SOCKS5 源会被 service 跳过
    from aiohttp_socks import ProxyConnector
except ImportError:  # pragma: no cover - 取决于运行环境
    ProxyConnector = None  # type: ignore[assignment]

logger = get_logger("validator")


def socks_supported() -> bool:
    """运行环境是否具备 SOCKS5 校验能力（依赖 aiohttp-socks）。"""
    return ProxyConnector is not None


class Validator:
    """代理校验器。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._socks_missing_logged = False
        # 单个代理的硬性上限（秒）：HTTP + HTTPS 两次探测各有 verify_timeout
        # 的预算，再留 2 秒余量。这个值是外层 asyncio.timeout 的兜底，
        # 不能依赖 aiohttp 的 ClientTimeout（见 validate 的说明）。
        self.hard_timeout = self.settings.verify_timeout * 2 + 2

    # ------------------------------------------------------------------ 探测
    async def _probe(
        self,
        session: aiohttp.ClientSession,
        url: str,
        proxy_url: str | None,
        *,
        connect: float | None = None,
    ) -> bool:
        connect_timeout = connect or min(self.settings.verify_timeout, 4)
        timeout = ClientTimeout(total=self.settings.verify_timeout, connect=connect_timeout)
        try:
            async with session.head(
                url, proxy=proxy_url, timeout=timeout, ssl=False, allow_redirects=False
            ) as response:
                if response.status < 400:
                    return True
        except (TimeoutError, aiohttp.ClientError, OSError, EOFError):
            # 连不上代理本身（拒绝连接/超时/握手失败），再发 GET 也不会成功，
            # 直接判定失败，避免每个死代理都白白消耗双倍超时时间。
            return False

        # 代理可用但目标站拒绝 HEAD（405/403 等），降级为 GET
        try:
            async with session.get(url, proxy=proxy_url, timeout=timeout, ssl=False, allow_redirects=False) as response:
                if response.status >= 400:
                    return False
                await response.content.read(1)
                return True
        except (TimeoutError, aiohttp.ClientError, OSError, EOFError):
            return False

    @staticmethod
    def format_ok(proxy: str) -> bool:
        return parse_proxy(proxy) is not None

    # ------------------------------------------------------------------ 校验
    async def validate(self, proxy: Proxy, session: aiohttp.ClientSession) -> Proxy:
        """校验单个代理并就地更新其状态字段。

        外层 ``asyncio.timeout`` 是必需的兜底：``ClientTimeout(total=...)`` 在
        SOCKS5 通道上并没有真正生效——实测配置 10s 的探测跑到 60s 才返回，少量
        这样的长尾会把整批巡检（并发 200）拖住，吞吐被压到 2.5 条/秒且与并发
        无关。这里用事件循环级超时保证任何代理都不可能超过 ``hard_timeout``。
        """
        try:
            async with asyncio.timeout(self.hard_timeout):
                return await self._validate(proxy, session)
        except TimeoutError:
            logger.debug("校验超时（硬上限 %.1fs）：%s", self.hard_timeout, proxy.proxy)
            proxy.mark_failure()
            return proxy

    async def _validate(self, proxy: Proxy, session: aiohttp.ClientSession) -> Proxy:
        if proxy.is_socks:
            return await self._validate_socks(proxy)

        proxy_url = proxy.as_url
        http_ok = await self._probe(session, self.settings.http_url, proxy_url)
        if not http_ok:
            proxy.mark_failure()
            return proxy

        https_ok = await self._probe(session, self.settings.https_url, proxy_url)
        proxy.mark_success(https_ok)
        return proxy

    async def _validate_socks(self, proxy: Proxy) -> Proxy:
        """通过 SOCKS5 通道校验；连接器绑定代理，因此每个代理独立会话。"""
        if ProxyConnector is None:
            if not self._socks_missing_logged:
                logger.warning("未安装 aiohttp-socks，跳过 SOCKS5 校验")
                self._socks_missing_logged = True
            proxy.mark_failure()
            return proxy

        try:
            connector = ProxyConnector.from_url(proxy.as_url, rdns=True, limit=4)
        except Exception as exc:  # 代理串非法
            logger.debug("SOCKS5 连接器创建失败 %s: %s", proxy.proxy, exc)
            proxy.mark_failure()
            return proxy

        timeout = ClientTimeout(total=self.settings.verify_timeout + 2)
        # trust_env 必须为 False：否则会套用环境变量里的 HTTP_PROXY/HTTPS_PROXY，
        # 流量被二次代理（实测 True 时 60 条全部失败，False 时 17 条可用）
        # connect 与 HTTP 路径保持一致取 4s：原先直接取 verify_timeout(8s)，
        # 等于把 total 预算全给握手，请求阶段只剩 0 秒余量。
        connect = min(float(self.settings.verify_timeout), 4.0)
        try:
            async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False) as session:
                if not await self._probe(session, self.settings.http_url, None, connect=connect):
                    proxy.mark_failure()
                    return proxy
                https_ok = await self._probe(session, self.settings.https_url, None, connect=connect)
                proxy.mark_success(https_ok)
                return proxy
        except (TimeoutError, aiohttp.ClientError, OSError, EOFError) as exc:
            logger.debug("SOCKS5 校验异常 %s: %s", proxy.proxy, exc)
            proxy.mark_failure()
            return proxy

    async def validate_many(self, proxies: Iterable[Proxy]) -> list[Proxy]:
        """并发校验一批代理，返回全部（含失败）结果。"""
        candidates = [p for p in proxies if self.format_ok(p.proxy)]
        if not candidates:
            return []

        concurrency = max(1, self.settings.check_concurrency)
        semaphore = asyncio.Semaphore(concurrency)
        rows: list[Proxy] = []

        async with build_session(limit=concurrency, total_timeout=self.settings.verify_timeout + 5) as session:

            async def run(proxy: Proxy) -> None:
                async with semaphore:
                    try:
                        rows.append(await self.validate(proxy, session))
                    except Exception as exc:  # 单个代理异常不应中断整批
                        logger.debug("校验异常 %s: %s", proxy.proxy, exc)
                        proxy.mark_failure()
                        rows.append(proxy)

            async with asyncio.TaskGroup() as group:
                for proxy in candidates:
                    group.create_task(run(proxy))

        valid = sum(1 for p in rows if p.last_status)
        logger.info("校验完成：%d 个代理，可用 %d 个（并发 %d）", len(rows), valid, concurrency)
        return rows
