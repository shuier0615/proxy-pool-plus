"""国内公开代理站点。

仅保留实测可解析的站点；快代理（JS 反爬）、站大爷（页面结构已改）、
稻壳代理（域名不可达）、66代理（API 502）均已移除。
"""

from __future__ import annotations

import re

import aiohttp

from fetcher.base import BaseFetcher, TextListFetcher, unique
from utils.proxy_parse import normalize

_IP_PORT_RE = re.compile(
    r"<td[^>]*>\s*(?:<[^>]+>\s*)*((?:\d{1,3}\.){3}\d{1,3})\s*(?:<[^>]+>\s*)*</td>"
    r"[\s\S]{0,200}?<td[^>]*>\s*(?:<[^>]+>\s*)*(\d{2,5})\s*(?:<[^>]+>\s*)*</td>"
)


class Ip89Fetcher(TextListFetcher):
    """89免费代理 https://www.89ip.cn/"""

    name = "ip89"
    url = "https://www.89ip.cn/"
    urls = tuple(f"https://www.89ip.cn/index_{page}.html" for page in range(1, 4))

    def parse(self, text: str | None) -> list[str]:  # type: ignore[override]
        if not text:
            return []
        return [p for ip, port in _IP_PORT_RE.findall(text) if (p := normalize(ip, port))]


class Ip3366Fetcher(BaseFetcher):
    """云代理 http://www.ip3366.net/"""

    name = "ip3366"
    url = "http://www.ip3366.net/"
    protocols = ("http",)

    async def fetch(self, session: aiohttp.ClientSession) -> list[str]:
        results: list[str] = []
        for stype in (1, 2):
            text = await self.get_text(session, f"http://www.ip3366.net/free/?stype={stype}&page=1")
            results.extend(unique(self.parse(text)))
        return unique(results)

    def parse(self, text: str | None) -> list[str]:  # type: ignore[override]
        if not text:
            return []
        return [p for ip, port in _IP_PORT_RE.findall(text) if (p := normalize(ip, port))]


class IhuanFetcher(BaseFetcher):
    """小幻代理 https://ip.ihuan.me/"""

    name = "ihuan"
    url = "https://ip.ihuan.me/"

    async def fetch(self, session: aiohttp.ClientSession) -> list[str]:
        # 首次请求用于获取 cookie，否则返回空列表
        await self.get_text(session, self.url)
        text = await self.get_text(session, self.url)
        tree = self.parse_html(text)
        if tree is None:
            return []
        results: list[str] = []
        for row in tree.xpath("//table[contains(@class,'table')]//tr"):
            cells = ["".join(td.xpath(".//text()")).strip() for td in row.xpath("./td")]
            if len(cells) >= 2 and (proxy := normalize(cells[0], cells[1])):
                results.append(proxy)
        return unique(results)


class KxdailiFetcher(BaseFetcher):
    """开心代理 http://www.kxdaili.com/"""

    name = "kxdaili"
    url = "http://www.kxdaili.com/dailiip.html"

    async def fetch(self, session: aiohttp.ClientSession) -> list[str]:
        results: list[str] = []
        for url in (
            "http://www.kxdaili.com/dailiip.html",
            "http://www.kxdaili.com/dailiip/2/1.html",
        ):
            text = await self.get_text(session, url)
            tree = self.parse_html(text)
            if tree is None:
                continue
            for row in tree.xpath("//table[contains(@class,'active')]//tr"):
                cells = ["".join(td.xpath("./text()")).strip() for td in row.xpath("./td")]
                if len(cells) >= 2 and (proxy := normalize(cells[0], cells[1])):
                    results.append(proxy)
        return unique(results)


class GoodipsFetcher(BaseFetcher):
    """谷德代理 https://www.goodips.com/"""

    name = "goodips"
    url = "https://www.goodips.com/"

    async def fetch(self, session: aiohttp.ClientSession) -> list[str]:
        text = await self.get_text(session, self.url)
        tree = self.parse_html(text)
        if tree is None:
            return []
        results: list[str] = []
        for block in tree.xpath("//div[contains(@class,'table-list')]"):
            cells = ["".join(li.xpath(".//text()")).strip() for li in block.xpath("./ul/li")]
            if len(cells) >= 2 and (proxy := normalize(cells[0], cells[1])):
                results.append(proxy)
        return unique(results)


class FreeVpnNodeFetcher(BaseFetcher):
    """FreeVPNNode https://cn.freevpnnode.com/free-proxy/"""

    name = "freevpnnode"
    url = "https://cn.freevpnnode.com/free-proxy/"

    async def fetch(self, session: aiohttp.ClientSession) -> list[str]:
        results: list[str] = []
        for page in range(1, 3):
            page_url = self.url if page == 1 else f"{self.url}?page={page}"
            text = await self.get_text(session, page_url)
            tree = self.parse_html(text)
            if tree is not None:
                for row in tree.xpath("//tr"):
                    cells = ["".join(td.xpath(".//text()")).strip() for td in row.xpath("./td")]
                    if len(cells) >= 2 and (proxy := normalize(cells[0], cells[1])):
                        results.append(proxy)
            results.extend(self.parse(text))
        return unique(results)


__all__ = [
    "FreeVpnNodeFetcher",
    "GoodipsFetcher",
    "Ip3366Fetcher",
    "Ip89Fetcher",
    "IhuanFetcher",
    "KxdailiFetcher",
]
