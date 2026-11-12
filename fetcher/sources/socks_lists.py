"""SOCKS5 公开代理清单。

均为社区持续维护的免费清单，按 ``socks5`` 协议入库（独立命名空间），
不会与同 ``host:port`` 的 HTTP 代理冲突。

实测（2026-11，单源抽 60 条做 SOCKS5 握手）：proxyscrape 33%、mmpx12 28%、
aliilapro 19%、zevtyardt 13%、thespeedx 12%、monosans 11%。注意握手成功不等于
能转发数据，真正可用率以校验器真实请求结果为准（通常再打 2~5 折）。
"""

from __future__ import annotations

from fetcher.base import TextListFetcher

_RAW = "https://raw.githubusercontent.com"


class _Socks5Fetcher(TextListFetcher):
    """SOCKS5 清单基类：统一声明协议与命名空间。"""

    scheme = "socks5"
    protocols = ("socks5",)


class ProxyScrapeSocks5Fetcher(_Socks5Fetcher):
    """proxyscrape v2 SOCKS5 接口（实测握手可用率最高）"""

    name = "proxyscrape_s5"
    url = "https://proxyscrape.com/"
    urls = ("https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all",)


class MonosansSocks5Fetcher(_Socks5Fetcher):
    """monosans/proxy-list SOCKS5（带存活检测）"""

    name = "monosans_s5"
    url = f"{_RAW}/monosans/proxy-list"
    urls = (f"{_RAW}/monosans/proxy-list/main/proxies/socks5.txt",)


class AliilaproSocks5Fetcher(_Socks5Fetcher):
    """ALIILAPRO/Proxy SOCKS5"""

    name = "aliilapro_s5"
    url = f"{_RAW}/ALIILAPRO/Proxy"
    urls = (f"{_RAW}/ALIILAPRO/Proxy/main/socks5.txt",)


class ZevtyardtSocks5Fetcher(_Socks5Fetcher):
    """zevtyardt/proxy-list SOCKS5（约 2.3 万条，规模最大）"""

    name = "zevtyardt_s5"
    url = f"{_RAW}/zevtyardt/proxy-list"
    urls = (f"{_RAW}/zevtyardt/proxy-list/main/socks5.txt",)


class TheSpeedXSocks5Fetcher(_Socks5Fetcher):
    """TheSpeedX/SOCKS-List SOCKS5"""

    name = "thespeedx_s5"
    url = f"{_RAW}/TheSpeedX/SOCKS-List"
    urls = (f"{_RAW}/TheSpeedX/SOCKS-List/master/socks5.txt",)


class Mmpx12Socks5Fetcher(_Socks5Fetcher):
    """mmpx12/proxy-list SOCKS5（小清单，实测握手可用率较高）"""

    name = "mmpx12_s5"
    url = f"{_RAW}/mmpx12/proxy-list"
    urls = (f"{_RAW}/mmpx12/proxy-list/master/socks5.txt",)


__all__ = [
    "AliilaproSocks5Fetcher",
    "Mmpx12Socks5Fetcher",
    "MonosansSocks5Fetcher",
    "ProxyScrapeSocks5Fetcher",
    "TheSpeedXSocks5Fetcher",
    "ZevtyardtSocks5Fetcher",
]
