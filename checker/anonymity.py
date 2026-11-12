"""代理匿名度判定。

通过代理访问一个会回显请求头的接口（默认 httpbin.org），看目标站实际收到的头：

* ``transparent``    目标站能看到客户端标识（``X-Forwarded-For`` / ``X-Real-IP`` /
  ``Forwarded`` 等），说明代理把你的真实 IP 一起转发了
* ``anonymous``      客户端 IP 被隐藏，但留下了代理痕迹（``Via`` /
  ``Proxy-Connection`` / 代理自身 IP）
* ``high_anonymous`` 两者都没有，最干净的一类

探测失败（超时、回显站不可达、返回非 JSON）**不写任何值**，保持空串，由接口层
输出默认值 ``unknown``。留空是为了下一轮还能重试——写死 ``unknown`` 就再也分不清
「没探测过」和「探测过但失败」了。
"""

from __future__ import annotations

import asyncio

import aiohttp

from logging_setup import get_logger
from models import Proxy
from utils.net import default_headers

try:  # 可选依赖：未安装时跳过 SOCKS5 的匿名度探测
    from aiohttp_socks import ProxyConnector
except ImportError:  # pragma: no cover - 取决于运行环境
    ProxyConnector = None  # type: ignore[assignment]

logger = get_logger("anonymity")

#: 三种判定结果
TRANSPARENT = "transparent"
ANONYMOUS = "anonymous"
HIGH_ANONYMOUS = "high_anonymous"
#: 未探测出结果时的展示值。只用于 API 输出，不写入 Redis。
DEFAULT_ANONYMOUS = "unknown"

#: 出现这些头说明客户端标识被转发，等同于透明代理
LEAK_HEADERS = frozenset(
    {
        "x-forwarded-for",
        "forwarded",
        "x-real-ip",
        "client-ip",
        "x-client-ip",
        "true-client-ip",
        "x-originating-ip",
    }
)
#: 只暴露「经过代理」这一事实，不泄露客户端 IP
TRACE_HEADERS = frozenset({"via", "proxy-connection", "x-proxy-id", "forwarded-for"})

#: 回显候选：httpbin 优先（结构最规整），httpbingo 兜底。
#: 只留两个——代理本身不通时换站也不会通，多个候选只会把尾延迟拖长。
ECHO_URLS: tuple[str, ...] = (
    "http://httpbin.org/get",
    "https://httpbingo.org/get",
)

#: 单次回显请求的超时。取 8s 而不是更短的 6s：跨境代理走完整 HTTP 往返 + JSON
#: 响应本来就慢，6s 下实测成功率只有 31%，8s 能到 47%，多等的 2s 只在失败时才付出。
_PROBE_TIMEOUT = 8.0


def _stringify(value: object) -> str:
    """httpbingo 会把重复的头返回成数组，这里统一压成逗号串。"""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def _extract(payload: object) -> tuple[dict[str, str], str]:
    """从回显 JSON 中取出 ``headers``（小写键）与 ``origin``。"""
    if not isinstance(payload, dict):
        return {}, ""
    raw_headers = payload.get("headers")
    if not isinstance(raw_headers, dict):
        return {}, ""
    headers = {str(key).lower(): _stringify(value) for key, value in raw_headers.items()}
    origin = payload.get("origin")
    if isinstance(origin, (list, tuple)):
        return headers, ", ".join(str(item) for item in origin)
    return headers, origin if isinstance(origin, str) else ""


def classify(headers: dict[str, str], origin: str, proxy_ip: str = "") -> str:
    """根据回显头判定匿名度。

    判定顺序有讲究：先看有没有泄露客户端标识（最严重），再看 origin 是否是
    多跳链路，最后才看代理痕迹。
    """
    if not headers:
        return ""
    if any(name in headers for name in LEAK_HEADERS):
        return TRANSPARENT
    # origin 形如 "1.2.3.4, 5.6.7.8"：链路上有人把客户端 IP 追加了进来
    hops = [part.strip() for part in origin.replace(";", ",").split(",") if part.strip()]
    if len(hops) > 1:
        return TRANSPARENT
    if any(name in headers for name in TRACE_HEADERS):
        return ANONYMOUS
    # 代理把自己的地址写进了某个头（如 Via: 1.1 1.2.3.4）
    if proxy_ip and any(proxy_ip in value for value in headers.values()):
        return ANONYMOUS
    return HIGH_ANONYMOUS


async def _get(session: aiohttp.ClientSession, url: str, *, proxy: str | None, timeout: float) -> object:
    """取回显 JSON，失败返回 ``None``。"""
    try:
        async with session.get(
            url,
            proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers=default_headers(),
            ssl=False,
            allow_redirects=True,
        ) as response:
            if response.status != 200:
                return None
            return await response.json(content_type=None)
    except Exception:  # noqa: BLE001 - 探测失败原因五花八门（含 python-socks 的 IncompleteReadError）
        return None


async def _detect_http(session: aiohttp.ClientSession, proxy: Proxy, urls: tuple[str, ...]) -> str:
    try:
        # 整体兜底：httpbin 的 ClientTimeout 经代理未必生效，这里再套一层事件循环级超时
        async with asyncio.timeout(_PROBE_TIMEOUT * len(urls)):
            for url in urls:
                payload = await _get(session, url, proxy=proxy.as_url, timeout=_PROBE_TIMEOUT)
                headers, origin = _extract(payload)
                if headers:
                    return classify(headers, origin, proxy.ip)
    except TimeoutError:
        return ""
    return ""


async def _detect_socks(proxy: Proxy, urls: tuple[str, ...]) -> str:
    """SOCKS5 通道绑定在连接器上，只能每个代理单独开会话。"""
    if ProxyConnector is None:
        return ""
    try:
        connector = ProxyConnector.from_url(proxy.as_url, rdns=True, limit=4)
    except Exception as exc:  # 代理串非法
        logger.debug("SOCKS5 连接器创建失败 %s: %s", proxy.proxy, exc)
        return ""
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT * len(urls) + 2):
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=_PROBE_TIMEOUT + 2),
                trust_env=False,
            ) as session:
                for url in urls:
                    payload = await _get(session, url, proxy=None, timeout=_PROBE_TIMEOUT)
                    headers, origin = _extract(payload)
                    if headers:
                        return classify(headers, origin, proxy.ip)
    except Exception:  # noqa: BLE001 - python-socks 会抛 IncompleteReadError 等非 OSError 异常
        return ""
    return ""


async def detect(proxy: Proxy, urls: tuple[str, ...] | None = None) -> str:
    """探测单个代理的匿名度，失败返回空串。"""
    from utils.net import build_session

    targets = urls or ECHO_URLS
    if proxy.is_socks:
        return await _detect_socks(proxy, targets)
    async with build_session(limit=8, total_timeout=_PROBE_TIMEOUT + 2) as session:
        return await _detect_http(session, proxy, targets)


async def detect_many(proxies: list[Proxy], *, concurrency: int = 32, urls: tuple[str, ...] | None = None) -> int:
    """并发探测一批代理，返回实际填充的条数。

    已带匿名度的代理会被跳过——重复探测既浪费请求，结果也不会变。
    """
    from utils.net import build_session

    targets = urls or ECHO_URLS
    pending = [p for p in proxies if not p.anonymous]
    if not pending:
        return 0

    http_items = [p for p in pending if not p.is_socks]
    socks_items = [p for p in pending if p.is_socks]
    filled = 0
    semaphore = asyncio.Semaphore(max(1, concurrency))

    if http_items:

        async def run_http(session: aiohttp.ClientSession, proxy: Proxy) -> str:
            async with semaphore:
                # 单个代理的任何异常都不应让整个 TaskGroup 崩掉
                try:
                    return await _detect_http(session, proxy, targets)
                except Exception:  # noqa: BLE001
                    return ""

        async with build_session(limit=max(8, concurrency), total_timeout=_PROBE_TIMEOUT + 5) as session:
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(run_http(session, proxy)) for proxy in http_items]
            for proxy, task in zip(http_items, tasks, strict=True):
                value = task.result()
                if value:
                    proxy.anonymous = value
                    filled += 1

    if socks_items:

        async def run_socks(proxy: Proxy) -> str:
            async with semaphore:
                try:
                    return await _detect_socks(proxy, targets)
                except Exception:  # noqa: BLE001
                    return ""

        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(run_socks(proxy)) for proxy in socks_items]
        for proxy, task in zip(socks_items, tasks, strict=True):
            value = task.result()
            if value:
                proxy.anonymous = value
                filled += 1

    logger.debug("匿名度探测：%d 个代理，命中 %d 个", len(pending), filled)
    return filled
