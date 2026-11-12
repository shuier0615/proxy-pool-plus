"""提供 JSON API 的公开代理源。"""

from __future__ import annotations

from typing import ClassVar

import aiohttp

from fetcher.base import BaseFetcher, unique
from utils.proxy_parse import normalize


class _JsonPairFetcher(BaseFetcher):
    """``data: [{ip, port, protocols?}, ...]`` 结构的通用解析。

    Geonode / Roundproxies 这类接口把 HTTP、HTTPS(需 TLS 连代理)、SOCKS4、SOCKS5
    混在同一个列表里返回（实测 HTTP 仅占 30%~40%），若不过滤会大量写入注定校验
    失败的条目，白白消耗校验配额。子类用 ``allowed_protocols`` 声明可接受的协议。
    """

    api = ""
    results_key = "data"
    #: 允许的代理协议；``None`` 表示不按协议过滤
    allowed_protocols: ClassVar[frozenset[str] | None] = None

    def _protocol_allowed(self, item: dict) -> bool:
        if self.allowed_protocols is None:
            return True
        raw = item.get("protocols", item.get("protocol", ""))
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, (list, tuple, set)):
            values = [str(value) for value in raw]
        else:
            values = []
        return any(value.lower() in self.allowed_protocols for value in values)

    async def fetch(self, session: aiohttp.ClientSession) -> list[str]:
        payload = await self.get_json(session, self.api)
        if not isinstance(payload, dict):
            return []
        results: list[str] = []
        for item in payload.get(self.results_key) or []:
            if not isinstance(item, dict) or not self._protocol_allowed(item):
                continue
            if proxy := normalize(str(item.get("ip", "")), item.get("port", 0)):
                results.append(proxy)
        return unique(results)


class GeonodeFetcher(_JsonPairFetcher):
    """Geonode Free Proxy https://geonode.com/"""

    name = "geonode"
    url = "https://geonode.com/"
    # filterLastChecked=10 只覆盖最近 10 分钟验活的代理，实测只剩 2 条全是 socks5；
    # 放宽到 60 分钟可拿到完整一页（约 500 条，其中 http 约 190 条）
    api = (
        "https://proxylist.geonode.com/api/proxy-list"
        "?filterLastChecked=60&page=1&limit=500&sort_by=lastChecked&sort_type=desc"
    )
    allowed_protocols = frozenset({"http"})


class RoundProxiesFetcher(_JsonPairFetcher):
    """Roundproxies https://roundproxies.com/free-proxy-list"""

    name = "roundproxies"
    url = "https://roundproxies.com/free-proxy-list"
    api = "https://roundproxies.com/api/get-free-proxies/?limit=100&page=1&sort_by=lastChecked&sort_type=desc"
    # 实测该接口返回 http 29% / socks5 35% / https 19% / socks4 17%
    allowed_protocols = frozenset({"http"})


class ScdnFetcher(BaseFetcher):
    """SCDN 代理接口 https://proxy.scdn.io/"""

    name = "scdn"
    url = "https://proxy.scdn.io/"
    api = "https://proxy.scdn.io/get_proxies.php?protocol=&country=&per_page=100&page=1"

    async def fetch(self, session: aiohttp.ClientSession) -> list[str]:
        payload = await self.get_json(session, self.api)
        if not isinstance(payload, dict):
            return []

        results: list[str] = []
        table_html = payload.get("table_html")
        if table_html:
            tree = self.parse_html(f"<table>{table_html}</table>")
            if tree is not None:
                for row in tree.xpath("//tr"):
                    cells = ["".join(td.xpath(".//text()")).strip() for td in row.xpath("./td")]
                    if len(cells) < 2:
                        continue
                    # 第 3 列是协议徽标（HTTP / SOCKS5），空协议按 HTTP 处理
                    if len(cells) >= 3 and cells[2] and cells[2].upper() != "HTTP":
                        continue
                    if proxy := normalize(cells[0], cells[1]):
                        results.append(proxy)

        if not results:
            for item in payload.get("data") or []:
                if isinstance(item, dict) and (proxy := normalize(str(item.get("ip", "")), item.get("port", 0))):
                    results.append(proxy)
        return unique(results)


class _ProxiflyBase(BaseFetcher):
    """Proxifly 官方 JSON 清单解析。

    数据结构：``[{proxy, protocol, ip, port, https, anonymity, score, geolocation}]``。
    其中 ``protocol`` 指代理自身协议（``https`` 表示"需要 TLS 连到代理"，
    与"能转发 https 目标"是两回事），``https`` 字段伴随 ``protocol=https`` 出现。
    本池只处理 ``http`` 协议条目，其余协议无法用 http 客户端直接使用。
    """

    name = ""
    url = "https://proxifly.dev/"
    api = ""
    timeout = 45.0
    #: 协议白名单
    allowed_protocols: ClassVar[frozenset[str]] = frozenset({"http"})
    #: 国家白名单，``None`` 表示不限制
    countries: ClassVar[frozenset[str] | None] = None

    async def fetch(self, session: aiohttp.ClientSession) -> list[str]:
        payload = await self.get_json(session, self.api)
        if not isinstance(payload, list):
            return []

        results: list[str] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            if str(item.get("protocol", "")).lower() not in self.allowed_protocols:
                continue
            if self.countries is not None:
                country = str((item.get("geolocation") or {}).get("country") or "").upper()
                if country not in self.countries:
                    continue
            host_port = str(item.get("proxy", "")).rsplit("://", 1)[-1]
            if not host_port and item.get("ip"):
                host_port = f"{item['ip']}:{item.get('port', 0)}"
            results.extend(self.parse(host_port))
        return unique(results)


class ProxiflyFetcher(_ProxiflyBase):
    """Proxifly 全量清单，仅保留亚太/北美常用区域 https://proxifly.dev/"""

    name = "proxifly"
    api = "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.json"
    countries = frozenset({"CN", "HK", "TW", "SG", "JP", "US"})


class ProxiflyCnFetcher(_ProxiflyBase):
    """Proxifly 中国区清单，实测可用率显著高于全量清单"""

    name = "proxifly_cn"
    api = "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/countries/CN/data.json"
    timeout = 30.0


__all__ = [
    "GeonodeFetcher",
    "ProxiflyCnFetcher",
    "ProxiflyFetcher",
    "RoundProxiesFetcher",
    "ScdnFetcher",
]
