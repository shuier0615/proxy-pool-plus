"""国际公开代理列表（纯文本）。

全部为社区持续维护的免费清单，直连 CDN 拉取，无需登录。
实测更新时间：2026-11。
"""

from __future__ import annotations

from fetcher.base import TextListFetcher

_RAW = "https://raw.githubusercontent.com"


class TheSpeedXFetcher(TextListFetcher):
    """TheSpeedX/PROXY-List（约 1.5 万条）"""

    name = "thespeedx"
    url = f"{_RAW}/TheSpeedX/PROXY-List"
    urls = (f"{_RAW}/TheSpeedX/PROXY-List/master/http.txt",)


class MonosansFetcher(TextListFetcher):
    """monosans/proxy-list（带存活检测，质量较高）"""

    name = "monosans"
    url = f"{_RAW}/monosans/proxy-list"
    urls = (f"{_RAW}/monosans/proxy-list/main/proxies/http.txt",)


class ClarketmFetcher(TextListFetcher):
    """clarketm/proxy-list"""

    name = "clarketm"
    url = f"{_RAW}/clarketm/proxy-list"
    urls = (f"{_RAW}/clarketm/proxy-list/master/proxy-list-raw.txt",)


class DatabayFetcher(TextListFetcher):
    """databay-labs/free-proxy-list（按协议/国家分目录）"""

    name = "databay"
    url = f"{_RAW}/databay-labs/free-proxy-list"
    urls = (f"{_RAW}/databay-labs/free-proxy-list/master/http.txt",)


class VakhovFetcher(TextListFetcher):
    """vakhov/fresh-proxy-list（每小时刷新）"""

    name = "vakhov"
    url = f"{_RAW}/vakhov/fresh-proxy-list"
    urls = (f"{_RAW}/vakhov/fresh-proxy-list/master/http.txt",)


class IpLocateFetcher(TextListFetcher):
    """iplocate/free-proxy-list"""

    name = "iplocate"
    url = f"{_RAW}/iplocate/free-proxy-list"
    urls = (f"{_RAW}/iplocate/free-proxy-list/main/protocols/http.txt",)


class OpenProxyListFetcher(TextListFetcher):
    """openproxylist.xyz（约 1.3 万条）"""

    name = "openproxylist"
    url = "https://openproxylist.xyz/"
    urls = ("https://api.openproxylist.xyz/http.txt",)


class Proxy4ParsingFetcher(TextListFetcher):
    """proxy4parsing/proxy-list"""

    name = "proxy4parsing"
    url = f"{_RAW}/proxy4parsing/proxy-list"
    urls = (f"{_RAW}/proxy4parsing/proxy-list/main/http.txt",)


class PrxChkFetcher(TextListFetcher):
    """prxchk/proxy-list（仅保留近 5 分钟验活结果）"""

    name = "prxchk"
    url = f"{_RAW}/prxchk/proxy-list"
    urls = (f"{_RAW}/prxchk/proxy-list/main/http.txt",)


class MuRongPigFetcher(TextListFetcher):
    """MuRongPIG/Proxy-Master"""

    name = "murongpig"
    url = f"{_RAW}/MuRongPIG/Proxy-Master"
    urls = (f"{_RAW}/MuRongPIG/Proxy-Master/main/http.txt",)


class ZevtyardtFetcher(TextListFetcher):
    """zevtyardt/proxy-list"""

    name = "zevtyardt"
    url = f"{_RAW}/zevtyardt/proxy-list"
    urls = (f"{_RAW}/zevtyardt/proxy-list/main/http.txt",)


class AliilaproFetcher(TextListFetcher):
    """ALIILAPRO/Proxy"""

    name = "aliilapro"
    url = f"{_RAW}/ALIILAPRO/Proxy"
    urls = (f"{_RAW}/ALIILAPRO/Proxy/main/http.txt",)


class HideIpMeFetcher(TextListFetcher):
    """zloi-user/hideip.me（格式 ip:port:国家，正则自动忽略尾部字段）"""

    name = "hideip"
    url = f"{_RAW}/zloi-user/hideip.me"
    urls = (f"{_RAW}/zloi-user/hideip.me/main/http.txt",)


class RoosterkidFetcher(TextListFetcher):
    """roosterkid/openproxylist（HTTPS 验活清单）"""

    name = "roosterkid"
    url = f"{_RAW}/roosterkid/openproxylist"
    urls = (f"{_RAW}/roosterkid/openproxylist/main/HTTPS_RAW.txt",)
    protocols = ("http", "https")


class ShiftyFetcher(TextListFetcher):
    """ShiftyTR/Proxy-List"""

    name = "shifty"
    url = f"{_RAW}/ShiftyTR/Proxy-List"
    urls = (f"{_RAW}/ShiftyTR/Proxy-List/master/http.txt",)


class Mmpx12Fetcher(TextListFetcher):
    """mmpx12/proxy-list"""

    name = "mmpx12"
    url = f"{_RAW}/mmpx12/proxy-list"
    urls = (f"{_RAW}/mmpx12/proxy-list/master/http.txt",)


class ProxyScrapeFetcher(TextListFetcher):
    """proxyscrape 官方 API（全球 HTTP）"""

    name = "proxyscrape"
    url = "https://proxyscrape.com/"
    urls = (
        "https://api.proxyscrape.com/v4/free-proxy-list/get"
        "?request=display_proxies&protocol=http&proxy_format=ipport&format=text",
    )


class ProxyScrapeCnFetcher(TextListFetcher):
    """proxyscrape 官方 API（中国区 HTTP）"""

    name = "proxyscrape_cn"
    url = "https://proxyscrape.com/"
    urls = (
        "https://api.proxyscrape.com/v4/free-proxy-list/get"
        "?request=display_proxies&country=CN&protocol=http&proxy_format=ipport&format=text",
    )


class ProxyScrapeV2Fetcher(TextListFetcher):
    """proxyscrape legacy v2 接口（与 v4 清单仅部分重合，独立约 600 条，可用率较高）"""

    name = "proxyscrape_v2"
    url = "https://proxyscrape.com/"
    urls = ("https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all",)


__all__ = [
    "AliilaproFetcher",
    "ClarketmFetcher",
    "DatabayFetcher",
    "HideIpMeFetcher",
    "IpLocateFetcher",
    "Mmpx12Fetcher",
    "MonosansFetcher",
    "MuRongPigFetcher",
    "OpenProxyListFetcher",
    "Proxy4ParsingFetcher",
    "ProxyScrapeCnFetcher",
    "ProxyScrapeFetcher",
    "ProxyScrapeV2Fetcher",
    "PrxChkFetcher",
    "RoosterkidFetcher",
    "ShiftyFetcher",
    "TheSpeedXFetcher",
    "VakhovFetcher",
    "ZevtyardtFetcher",
]
