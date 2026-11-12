"""SOCKS5 支持测试：模型 / 存储隔离 / 校验分流 / 抓取路由（不触网）。"""

from __future__ import annotations

import asyncio
import dataclasses

import aiohttp

from checker import validator as validator_mod
from checker.validator import Validator
from config import get_settings
from fetcher.base import BaseFetcher
from models import Proxy
from service import ProxyPoolService


def run(coro):
    return asyncio.run(coro)


class TestProxyScheme:
    def test_default_scheme_is_http(self):
        proxy = Proxy("1.2.3.4:8080")
        assert proxy.scheme == "http"
        assert proxy.is_socks is False
        assert proxy.as_url == "http://1.2.3.4:8080"

    def test_socks_scheme_url(self):
        proxy = Proxy("1.2.3.4:1080", scheme="socks5")
        assert proxy.is_socks is True
        assert proxy.as_url == "socks5://1.2.3.4:1080"

    def test_socks_url_keeps_auth(
        self,
    ):
        proxy = Proxy("user:pwd@1.2.3.4:1080", scheme="socks5")
        assert proxy.as_url == "socks5://user:pwd@1.2.3.4:1080"

    def test_roundtrip_json_keeps_scheme(self):
        proxy = Proxy("1.2.3.4:1080", scheme="socks5", https=True, sources={"s5"})
        restored = Proxy.from_json(proxy.to_json())
        assert restored.scheme == "socks5"
        assert restored.https is True
        assert restored.to_dict()["scheme"] == "socks5"

    def test_old_payload_without_scheme(self):
        restored = Proxy.from_dict({"proxy": "1.2.3.4:8080", "https": False})
        assert restored.scheme == "http"


class TestSocksStorage:
    def test_same_host_port_coexist(self, store):
        """同一 host:port 既可能是 HTTP 也可能是 SOCKS5，命名空间必须隔离。"""
        store.put(Proxy("1.2.3.4:1080"))
        store.put(Proxy("1.2.3.4:1080", scheme="socks5"))

        assert store.count()["total"] == 1
        assert store.count()["socks5"] == 1
        assert store.count()["grand_total"] == 2
        assert Proxy.from_json(store.get()).scheme == "http"
        assert Proxy.from_json(store.get(scheme="socks5")).scheme == "socks5"

    def test_count_socks_https(self, store):
        store.put(Proxy("1.1.1.1:1080", scheme="socks5", https=True))
        store.put(Proxy("2.2.2.2:1080", scheme="socks5", https=False))
        counts = store.count()
        assert counts["socks5"] == 2
        assert counts["socks5_https"] == 1
        assert counts["total"] == 0

    def test_all_all_schemes(self, store):
        store.put(Proxy("1.1.1.1:1080"))
        store.put(Proxy("2.2.2.2:1080", scheme="socks5"))
        assert len(store.all()) == 1
        assert len(store.all(scheme="socks5")) == 1
        assert len(store.all(scheme="all")) == 2

    def test_pop_socks(self, store):
        store.put(Proxy("2.2.2.2:1080", scheme="socks5"))
        assert store.pop(scheme="socks5")
        assert store.pop(scheme="socks5") is None

    def test_delete_any_hits_both(self, store):
        store.put(Proxy("1.1.1.1:1080"))
        store.put(Proxy("1.1.1.1:1080", scheme="socks5"))
        assert sorted(store.delete_any("1.1.1.1:1080")) == ["http", "socks5"]
        assert store.count()["grand_total"] == 0

    def test_exists_many_per_scheme(self, store):
        store.put(Proxy("1.1.1.1:1080", scheme="socks5"))
        assert store.exists_many(["1.1.1.1:1080"], "socks5") == {"1.1.1.1:1080"}
        assert store.exists_many(["1.1.1.1:1080"], "http") == set()

    def test_reindex_single_scheme(self, store, fake_redis):
        store.put(Proxy("1.1.1.1:1080", scheme="socks5", https=True))
        fake_redis.delete("test_proxy:s5:all")
        assert store.reindex("socks5") == {"total": 1, "https": 1}
        assert fake_redis.scard("test_proxy:s5:all") == 1


class FakeConnector:
    """替代 aiohttp_socks.ProxyConnector，记录被转换的代理 URL。"""

    used: list[str] = []

    @classmethod
    def from_url(cls, url, **kwargs):
        cls.used.append(url)
        return aiohttp.TCPConnector()


class TestSocksValidator:
    def test_socks_proxy_uses_connector(self, monkeypatch):
        FakeConnector.used = []
        monkeypatch.setattr(validator_mod, "ProxyConnector", FakeConnector)

        seen: list[str | None] = []

        async def fake_probe(self, session, url, proxy_url, **kwargs):
            seen.append(proxy_url)
            return True

        monkeypatch.setattr(Validator, "_probe", fake_probe)
        proxy = Proxy("1.2.3.4:1080", scheme="socks5")

        result = run(Validator(get_settings()).validate(proxy, None))
        assert FakeConnector.used == ["socks5://1.2.3.4:1080"]
        assert seen == [None, None]  # SOCKS 通道由连接器承担，不能再传 proxy 参数
        assert result.last_status is True

    def test_socks_probe_failure(self, monkeypatch):
        monkeypatch.setattr(validator_mod, "ProxyConnector", FakeConnector)

        async def fake_probe(self, session, url, proxy_url, **kwargs):
            return False

        monkeypatch.setattr(Validator, "_probe", fake_probe)
        proxy = Proxy("1.2.3.4:1080", scheme="socks5")

        result = run(Validator(get_settings()).validate(proxy, None))
        assert result.last_status is False
        assert result.fail_count == 1

    def test_missing_dependency_marks_failure(self, monkeypatch):
        monkeypatch.setattr(validator_mod, "ProxyConnector", None)
        proxy = Proxy("1.2.3.4:1080", scheme="socks5")

        result = run(Validator(get_settings()).validate(proxy, None))
        assert result.last_status is False

    def test_http_proxy_unaffected(self, monkeypatch):
        calls: list[str | None] = []

        async def fake_probe(self, session, url, proxy_url, **kwargs):
            calls.append(proxy_url)
            return True

        monkeypatch.setattr(Validator, "_probe", fake_probe)
        proxy = Proxy("1.2.3.4:8080")
        run(Validator(get_settings()).validate(proxy, None))
        assert calls == ["http://1.2.3.4:8080", "http://1.2.3.4:8080"]


class SocksSource(BaseFetcher):
    name = "fake_s5"
    url = "https://fake.local"
    scheme = "socks5"

    async def fetch(self, session):
        return ["9.9.9.1:1080"]


class HttpSource(BaseFetcher):
    name = "fake_http"
    url = "https://fake.local"

    async def fetch(self, session):
        return ["8.8.8.1:80"]


class PassValidator:
    async def validate_many(self, proxies):
        for proxy in proxies:
            proxy.mark_success(False)
        return list(proxies)


def make_service(store, monkeypatch, sources, **overrides):
    settings = dataclasses.replace(get_settings(), **overrides)
    service = ProxyPoolService(settings=settings, store=store, validator=PassValidator())
    monkeypatch.setattr("service.discover_sources", lambda exclude=(): sources)
    return service


class TestSocksFetch:
    def test_socks_source_stored_in_own_namespace(self, store, monkeypatch):
        service = make_service(store, monkeypatch, [HttpSource, SocksSource])
        report = run(service.fetch())

        assert report.stored == 2
        counts = store.count()
        assert counts["total"] == 1
        assert counts["socks5"] == 1
        assert Proxy.from_json(store.get()).scheme == "http"
        assert Proxy.from_json(store.get(scheme="socks5")).scheme == "socks5"

    def test_socks_disabled_by_setting(self, store, monkeypatch):
        service = make_service(store, monkeypatch, [HttpSource, SocksSource], socks_max_new=0)
        report = run(service.fetch())

        assert "fake_s5" not in report.per_source
        assert store.count()["socks5"] == 0

    def test_socks_candidate_cap(self, store, monkeypatch):
        class BigSocksSource(BaseFetcher):
            name = "big_s5"
            url = "https://fake.local"
            scheme = "socks5"

            async def fetch(self, session):
                return [f"9.9.{i // 250}.{i % 250 + 1}:1080" for i in range(50)]

        service = make_service(store, monkeypatch, [BigSocksSource], socks_max_new=10)
        report = run(service.fetch())
        assert report.candidates == 10
