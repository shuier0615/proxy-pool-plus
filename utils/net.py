"""异步 HTTP 工具。

统一封装超时、重试、UA 轮换与响应解码，供代理源与校验器复用。
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from aiohttp import ClientTimeout

from logging_setup import get_logger

logger = get_logger("http")

# GitHub raw 在国内网络经常被阻断/丢包，且大量代理清单源都托管在 GitHub，
# 源站不可达会直接导致整批源失效。这里按下述顺序尝试镜像：
# 前缀式反代（实时转发，数据最新）> jsDelivr（有缓存，兜底）> 原始地址。
GITHUB_RAW_HOST = "raw.githubusercontent.com"
PREFIX_MIRRORS = (
    "https://ghproxy.net/",
    "https://gh-proxy.com/",
)
JSDELIVR_MIRRORS = (
    "https://fastly.jsdelivr.net/gh/",
    "https://gcore.jsdelivr.net/gh/",
)

USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
)


def default_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }
    if extra:
        headers.update(extra)
    return headers


def build_session(*, limit: int = 100, total_timeout: float = 30.0) -> aiohttp.ClientSession:
    """创建共享会话。连接数上限即全局并发上限。"""
    connector = aiohttp.TCPConnector(
        limit=limit,
        limit_per_host=0,
        ttl_dns_cache=300,
        enable_cleanup_closed=True,
    )
    return aiohttp.ClientSession(
        connector=connector,
        timeout=ClientTimeout(total=total_timeout),
        trust_env=True,
    )


def _decode(raw: bytes, charset: str | None) -> str:
    if charset:
        try:
            return raw.decode(charset, errors="replace")
        except (LookupError, UnicodeError):
            pass
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def github_mirrors(url: str) -> list[str]:
    """为 GitHub raw 链接生成镜像候选；其他链接原样返回单元素列表。

    例：``https://raw.githubusercontent.com/a/b/main/c.txt`` →
    ghproxy → gh-proxy → fastly → gcore → 原始地址。
    """
    parts = urlsplit(url)
    if parts.netloc != GITHUB_RAW_HOST:
        return [url]

    candidates = [f"{prefix}{url}" for prefix in PREFIX_MIRRORS]
    # jsDelivr 需要 owner/repo@branch/path 形式，路径层级不足时跳过
    segments = parts.path.lstrip("/").split("/", 3)
    if len(segments) == 4 and all(segments):
        owner, repo, branch, path = segments
        candidates += [f"{host}{owner}/{repo}@{branch}/{path}" for host in JSDELIVR_MIRRORS]
    candidates.append(url)
    return candidates


async def fetch_text_mirrors(
    session: aiohttp.ClientSession,
    url: str,
    *,
    timeout: float = 15.0,
    headers: dict[str, str] | None = None,
    proxy: str | None = None,
) -> str:
    """按镜像顺序取文本，首个成功即返回；总耗时不超过 ``timeout``。

    非 GitHub 地址等价于单次 :func:`fetch_text` 调用，不产生额外开销。
    """
    candidates = github_mirrors(url)
    if len(candidates) == 1:
        return await fetch_text(session, url, timeout=timeout, headers=headers, proxy=proxy)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    for candidate in candidates:
        remaining = deadline - loop.time()
        if remaining <= 1.0:
            break
        text = await fetch_text(
            session,
            candidate,
            timeout=min(remaining, 8.0),
            retries=0,
            headers=headers,
            proxy=proxy,
        )
        if text:
            if candidate != url:
                logger.debug("镜像命中 %s -> %s", url, candidate)
            return text
    return ""


async def fetch_text(
    session: aiohttp.ClientSession,
    url: str,
    *,
    timeout: float = 15.0,
    retries: int = 1,
    headers: dict[str, str] | None = None,
    proxy: str | None = None,
) -> str:
    """GET 文本，失败返回空串（不向上抛出，避免单个源拖垮整轮抓取）。"""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            async with session.get(
                url,
                headers=headers or default_headers(),
                timeout=ClientTimeout(total=timeout),
                proxy=proxy,
                ssl=False,
                allow_redirects=True,
            ) as response:
                if response.status >= 400:
                    raise aiohttp.ClientResponseError(
                        response.request_info,
                        response.history,
                        status=response.status,
                        message=f"HTTP {response.status}",
                    )
                raw = await response.read()
                return _decode(raw, response.charset)
        except (TimeoutError, aiohttp.ClientError, OSError, EOFError) as exc:
            last_error = exc
            if attempt < retries:
                await asyncio.sleep(0.5 * (attempt + 1))
    logger.debug("请求失败 %s: %s", url, last_error)
    return ""


async def fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    timeout: float = 15.0,
    retries: int = 1,
    headers: dict[str, str] | None = None,
    mirrors: bool = False,
) -> Any:
    if mirrors:
        text = await fetch_text_mirrors(session, url, timeout=timeout, headers=headers)
    else:
        text = await fetch_text(session, url, timeout=timeout, retries=retries, headers=headers)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.debug("JSON 解析失败: %s", url)
        return None
