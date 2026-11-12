"""各代理源解析逻辑测试（不触网）。"""

from __future__ import annotations

import asyncio

from fetcher.sources.api_sources import (
    GeonodeFetcher,
    ProxiflyCnFetcher,
    ProxiflyFetcher,
    RoundProxiesFetcher,
    ScdnFetcher,
)
from fetcher.sources.cn_sites import (
    FreeVpnNodeFetcher,
    GoodipsFetcher,
    IhuanFetcher,
    Ip89Fetcher,
    Ip3366Fetcher,
    KxdailiFetcher,
)
from fetcher.sources.raw_lists import MonosansFetcher, ProxyScrapeV2Fetcher, TheSpeedXFetcher


def run(coro):
    return asyncio.run(coro)


IP89_HTML = """
<table><tbody>
<tr><td>1.2.3.4</td><td>8080</td><td>中国</td></tr>
<tr><td>5.6.7.8</td><td>3128</td><td>美国</td></tr>
<tr><td>10.0.0.1</td><td>80</td><td>内网</td></tr>
</tbody></table>
"""

IHUAN_HTML = """
<table class="table table-hover table-bordered">
<tr><th>IP</th><th>PORT</th></tr>
<tr><td>1.2.3.4</td><td>8080</td><td>中国</td></tr>
</table>
"""

KXDAILI_HTML = """
<table class="active">
<tr><th>IP</th><th>端口</th></tr>
<tr><td>1.2.3.4</td><td>8080</td><td>高匿</td></tr>
</table>
"""

GOODIPS_HTML = """
<div class="table-list"><ul><li>1.2.3.4</li><li>8080</li><li>北京</li></ul></div>
<div class="table-list"><ul><li>5.6.7.8</li><li>3128</li><li>上海</li></ul></div>
"""

FREEVPN_HTML = """
<table><tr><th>IP</th><th>Port</th></tr>
<tr><td>1.2.3.4</td><td>8080</td><td>**</td><td>US</td></tr>
</table>
"""


class TestCnSites:
    def test_ip89(self):
        assert Ip89Fetcher().parse(IP89_HTML) == ["1.2.3.4:8080", "5.6.7.8:3128"]

    def test_ip3366_uses_same_table_layout(self):
        assert Ip3366Fetcher().parse(IP89_HTML) == ["1.2.3.4:8080", "5.6.7.8:3128"]

    def test_ihuan(self, monkeypatch):
        async def fake_get_text(self, session, url, **kwargs):
            return IHUAN_HTML

        monkeypatch.setattr(IhuanFetcher, "get_text", fake_get_text)
        assert run(IhuanFetcher().fetch(None)) == ["1.2.3.4:8080"]

    def test_kxdaili(self, monkeypatch):
        async def fake_get_text(self, session, url, **kwargs):
            return KXDAILI_HTML

        monkeypatch.setattr(KxdailiFetcher, "get_text", fake_get_text)
        assert run(KxdailiFetcher().fetch(None)) == ["1.2.3.4:8080"]

    def test_goodips(self, monkeypatch):
        async def fake_get_text(self, session, url, **kwargs):
            return GOODIPS_HTML

        monkeypatch.setattr(GoodipsFetcher, "get_text", fake_get_text)
        assert run(GoodipsFetcher().fetch(None)) == ["1.2.3.4:8080", "5.6.7.8:3128"]

    def test_freevpnnode(self, monkeypatch):
        async def fake_get_text(self, session, url, **kwargs):
            return FREEVPN_HTML

        monkeypatch.setattr(FreeVpnNodeFetcher, "get_text", fake_get_text)
        assert run(FreeVpnNodeFetcher().fetch(None)) == ["1.2.3.4:8080"]


class TestRawLists:
    def test_text_list_source(self, monkeypatch):
        async def fake_get_text(self, session, url, **kwargs):
            return "8.8.8.8:80\r\n1.1.1.1:8080\r\n1.1.1.1:8080"

        monkeypatch.setattr(MonosansFetcher, "get_text", fake_get_text)
        assert run(MonosansFetcher().fetch(None)) == ["8.8.8.8:80", "1.1.1.1:8080"]

    def test_source_metadata(self):
        assert TheSpeedXFetcher.name == "thespeedx"
        assert TheSpeedXFetcher.urls[0].startswith("https://raw.githubusercontent.com")

    def test_proxyscrape_v2_parses_crlf_list(self, monkeypatch):
        async def fake_get_text(self, session, url, **kwargs):
            return "185.135.69.34:80\r\n203.205.49.2:10099\r\n"

        monkeypatch.setattr(ProxyScrapeV2Fetcher, "get_text", fake_get_text)
        assert run(ProxyScrapeV2Fetcher().fetch(None)) == ["185.135.69.34:80", "203.205.49.2:10099"]


class TestJsonSources:
    def test_geonode_filters_non_http_protocols(self, monkeypatch):
        async def fake_get_json(self, session, url, **kwargs):
            assert "filterLastChecked=60" in url
            return {
                "data": [
                    {"ip": "1.2.3.4", "port": "8080", "protocols": ["http"]},
                    {"ip": "5.6.7.8", "port": "1080", "protocols": ["socks5"]},
                    {"ip": "9.9.9.9", "port": "8080", "protocols": ["https"]},
                    {"ip": "7.7.7.7", "port": "1080", "protocols": ["socks4"]},
                    {"ip": "10.0.0.1", "port": "80", "protocols": ["http"]},
                ]
            }

        monkeypatch.setattr(GeonodeFetcher, "get_json", fake_get_json)
        assert run(GeonodeFetcher().fetch(None)) == ["1.2.3.4:8080"]

    def test_roundproxies_filters_non_http_protocols(self, monkeypatch):
        async def fake_get_json(self, session, url, **kwargs):
            return {
                "data": [
                    {"ip": "5.6.7.8", "port": 3128, "protocols": ["http"]},
                    {"ip": "5.6.7.9", "port": 1080, "protocols": ["socks5"]},
                ]
            }

        monkeypatch.setattr(RoundProxiesFetcher, "get_json", fake_get_json)
        assert run(RoundProxiesFetcher().fetch(None)) == ["5.6.7.8:3128"]

    def test_roundproxies_accepts_string_protocol(self, monkeypatch):
        async def fake_get_json(self, session, url, **kwargs):
            return {"data": [{"ip": "5.6.7.8", "port": 3128, "protocol": "HTTP"}]}

        monkeypatch.setattr(RoundProxiesFetcher, "get_json", fake_get_json)
        assert run(RoundProxiesFetcher().fetch(None)) == ["5.6.7.8:3128"]

    def test_scdn_table_html_skips_socks_rows(self, monkeypatch):
        async def fake_get_json(self, session, url, **kwargs):
            return {
                "table_html": (
                    '<tr><td class="cell-ip">1.2.3.4</td><td>8080</td>'
                    "<td><span>HTTP</span></td></tr>"
                    '<tr><td class="cell-ip">5.6.7.8</td><td>1080</td>'
                    "<td><span>SOCKS5</span></td></tr>"
                )
            }

        monkeypatch.setattr(ScdnFetcher, "get_json", fake_get_json)
        assert run(ScdnFetcher().fetch(None)) == ["1.2.3.4:8080"]

    def test_scdn_table_html_two_columns(self, monkeypatch):
        async def fake_get_json(self, session, url, **kwargs):
            return {"table_html": '<tr><td class="cell-ip">1.2.3.4</td><td>8080</td></tr>'}

        monkeypatch.setattr(ScdnFetcher, "get_json", fake_get_json)
        assert run(ScdnFetcher().fetch(None)) == ["1.2.3.4:8080"]

    def test_scdn_data_fallback(self, monkeypatch):
        async def fake_get_json(self, session, url, **kwargs):
            return {"data": [{"ip": "1.2.3.4", "port": "8080"}]}

        monkeypatch.setattr(ScdnFetcher, "get_json", fake_get_json)
        assert run(ScdnFetcher().fetch(None)) == ["1.2.3.4:8080"]

    def test_proxifly_filters_protocol_and_country(self, monkeypatch):
        async def fake_get_json(self, session, url, **kwargs):
            return [
                {"proxy": "http://1.2.3.4:8080", "protocol": "http", "geolocation": {"country": "CN"}},
                {"proxy": "socks5://1.2.3.5:1080", "protocol": "socks5", "geolocation": {"country": "CN"}},
                {"proxy": "http://1.2.3.6:8080", "protocol": "http", "geolocation": {"country": "BR"}},
                {"proxy": "http://1.2.3.7:8080", "protocol": "http", "geolocation": {"country": "US"}},
            ]

        monkeypatch.setattr(ProxiflyFetcher, "get_json", fake_get_json)
        assert run(ProxiflyFetcher().fetch(None)) == ["1.2.3.4:8080", "1.2.3.7:8080"]

    def test_proxifly_ignores_tls_proxy_protocol(self, monkeypatch):
        """protocol=https 表示需要 TLS 连到代理，http 客户端无法直接使用。"""

        async def fake_get_json(self, session, url, **kwargs):
            return [
                {"proxy": "https://1.2.3.4:4443", "protocol": "https", "geolocation": {"country": "CN"}},
                {"proxy": "http://1.2.3.5:8080", "protocol": "http", "geolocation": {"country": "CN"}},
            ]

        monkeypatch.setattr(ProxiflyFetcher, "get_json", fake_get_json)
        assert run(ProxiflyFetcher().fetch(None)) == ["1.2.3.5:8080"]

    def test_proxifly_cn_keeps_all_countries(self, monkeypatch):
        async def fake_get_json(self, session, url, **kwargs):
            return [
                {"proxy": "http://1.2.3.4:8080", "protocol": "http", "geolocation": {"country": "CN"}},
                {"proxy": "http://1.2.3.5:8080", "protocol": "http", "geolocation": {}},
                {"proxy": "socks5://1.2.3.6:1080", "protocol": "socks5", "geolocation": {"country": "CN"}},
            ]

        monkeypatch.setattr(ProxiflyCnFetcher, "get_json", fake_get_json)
        assert run(ProxiflyCnFetcher().fetch(None)) == ["1.2.3.4:8080", "1.2.3.5:8080"]

    def test_proxifly_uses_ip_port_when_proxy_missing(self, monkeypatch):
        async def fake_get_json(self, session, url, **kwargs):
            return [{"ip": "1.2.3.4", "port": 8080, "protocol": "http", "geolocation": {"country": "CN"}}]

        monkeypatch.setattr(ProxiflyFetcher, "get_json", fake_get_json)
        assert run(ProxiflyFetcher().fetch(None)) == ["1.2.3.4:8080"]

    def test_json_source_handles_bad_payload(self, monkeypatch):
        async def fake_get_json(self, session, url, **kwargs):
            return None

        monkeypatch.setattr(GeonodeFetcher, "get_json", fake_get_json)
        assert run(GeonodeFetcher().fetch(None)) == []
