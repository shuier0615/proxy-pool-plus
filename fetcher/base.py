"""代理源基类与解析混入。

新增代理源只需继承 :class:`TextListFetcher` 并声明 ``urls``，
或继承 :class:`BaseFetcher` 实现自定义 ``fetch``。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from typing import ClassVar

import aiohttp
from lxml import etree

from logging_setup import get_logger
from utils.net import fetch_json, fetch_text_mirrors
from utils.proxy_parse import parse_proxies

logger = get_logger("fetcher")


def unique(items: Iterable[str]) -> list[str]:
    """按原顺序去重。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


class BaseFetcher(ABC):
    """所有代理源的基类。"""

    name: ClassVar[str] = ""
    url: ClassVar[str] = ""
    enabled: ClassVar[bool] = True
    timeout: ClassVar[float] = 15.0
    #: 该源产出的代理协议：``http`` 或 ``socks5``，决定入库命名空间与校验方式
    scheme: ClassVar[str] = "http"
    #: 该源声明支持的协议，便于后续按需筛选（http / https / socks）
    protocols: ClassVar[tuple[str, ...]] = ("http",)

    @abstractmethod
    async def fetch(self, session: aiohttp.ClientSession) -> list[str]:
        """返回 ``host:port`` 列表，异常由调用方统一捕获。"""
        raise NotImplementedError

    async def get_text(self, session: aiohttp.ClientSession, url: str, **kwargs) -> str:
        """取文本；GitHub raw 地址会自动切换可用镜像。"""
        return await fetch_text_mirrors(session, url, timeout=kwargs.pop("timeout", self.timeout), **kwargs)

    async def get_json(self, session: aiohttp.ClientSession, url: str, **kwargs):
        return await fetch_json(session, url, timeout=kwargs.pop("timeout", self.timeout), mirrors=True, **kwargs)

    @staticmethod
    def parse(text: str | None) -> list[str]:
        return parse_proxies(text)

    @staticmethod
    def parse_html(text: str | None) -> etree._Element | None:
        if not text:
            return None
        try:
            return etree.HTML(text)
        except (etree.ParserError, ValueError):
            return None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} name={self.name!r}>"


class TextListFetcher(BaseFetcher):
    """纯文本列表源：HTTP 响应体按行（或任意分隔）包含 ``host:port``。"""

    urls: ClassVar[Sequence[str]] = ()

    async def fetch(self, session: aiohttp.ClientSession) -> list[str]:
        results: list[str] = []
        # 多地址源逐个取，单个地址失败不影响其余地址已拿到的数据
        for url in self.urls:
            try:
                text = await self.get_text(session, url)
            except Exception as exc:
                logger.warning("源 %s 子地址失败 %s: %s", self.name, url, exc)
                continue
            results.extend(self.parse(text))
        return unique(results)


__all__ = ["BaseFetcher", "TextListFetcher", "unique"]
