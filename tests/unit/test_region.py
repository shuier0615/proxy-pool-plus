"""地域补全测试（不触网）。"""

from __future__ import annotations

import asyncio

import pytest

from checker import region
from models import Proxy


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_cache():
    region.clear_cache()
    yield
    region.clear_cache()


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    async def json(self, content_type=None):
        return self.payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class TestRegionName:
    def test_known_code(self):
        assert region.region_name("CN") == "中国"
        assert region.region_name("us") == "美国"
        assert region.region_name("HK") == "中国香港"

    def test_unknown_code_returns_itself(self):
        assert region.region_name("ZZ") == "ZZ"

    def test_empty(self):
        assert region.region_name("") == ""


class TestLookup:
    def test_returns_country_code(self, monkeypatch):
        async def fake(session, url, **kwargs):
            assert "/1.2.3.4" in url
            return {"countryCode": "US"}

        monkeypatch.setattr(region, "fetch_json", fake)
        assert run(region.lookup("1.2.3.4", None)) == "US"

    def test_missing_field_returns_empty(self, monkeypatch):
        async def fake(session, url, **kwargs):
            return {"country": "United States"}

        monkeypatch.setattr(region, "fetch_json", fake)
        assert run(region.lookup("1.2.3.4", None)) == ""

    def test_non_dict_payload_returns_empty(self, monkeypatch):
        async def fake(session, url, **kwargs):
            return None

        monkeypatch.setattr(region, "fetch_json", fake)
        assert run(region.lookup("1.2.3.4", None)) == ""

    def test_result_is_cached(self, monkeypatch):
        calls = []

        async def fake(session, url, **kwargs):
            calls.append(url)
            return {"countryCode": "JP"}

        monkeypatch.setattr(region, "fetch_json", fake)
        assert run(region.lookup("1.2.3.4", None)) == "JP"
        assert run(region.lookup("1.2.3.4", None)) == "JP"
        assert len(calls) == 1


class TestBatch:
    def test_parses_success_rows(self):
        payload = [
            {"status": "success", "countryCode": "us", "query": "1.1.1.1"},
            {"status": "fail", "query": "2.2.2.2"},
            "garbage",
        ]
        session = type("S", (), {"post": staticmethod(lambda *a, **k: FakeResponse(payload))})
        assert run(region._batch(["1.1.1.1", "2.2.2.2"], session, timeout=1.0)) == {"1.1.1.1": "US"}

    def test_non_200_returns_empty(self):
        session = type("S", (), {"post": staticmethod(lambda *a, **k: FakeResponse([], status=429))})
        assert run(region._batch(["1.1.1.1"], session, timeout=1.0)) == {}


class TestLookupMany:
    def test_fills_from_batch(self, monkeypatch):
        async def fake_batch(ips, session, *, timeout):
            return dict.fromkeys(ips, "CN")

        monkeypatch.setattr(region, "_batch", fake_batch)
        proxy = Proxy("8.8.8.1:80")
        assert run(region.lookup_many([("8.8.8.1", proxy)], None)) == 1
        assert proxy.region == "中国"

    def test_falls_back_to_single_lookup(self, monkeypatch):
        """批量接口漏掉的 IP 要逐条兜底，否则会永久留空。"""
        asked = []

        async def fake_batch(ips, session, *, timeout):
            return {}

        async def fake_lookup(ip, session, **_kwargs):
            asked.append(ip)
            return "JP"

        monkeypatch.setattr(region, "_batch", fake_batch)
        monkeypatch.setattr(region, "lookup", fake_lookup)
        proxy = Proxy("8.8.8.1:80")

        run(region.lookup_many([("8.8.8.1", proxy)], None))

        assert asked == ["8.8.8.1"]
        assert proxy.region == "日本"

    def test_fills_only_missing_region(self, monkeypatch):
        asked: list[str] = []

        async def fake_lookup(ip, session, **_kwargs):
            asked.append(ip)
            return "CN"

        async def fake_batch(ips, session, *, timeout):
            return {}

        monkeypatch.setattr(region, "_batch", fake_batch)
        monkeypatch.setattr(region, "lookup", fake_lookup)
        filled = Proxy("8.8.8.1:80")
        known = Proxy("8.8.8.2:80", region="US")

        run(region.lookup_many([("8.8.8.1", filled), ("8.8.8.2", known)], None))

        assert filled.region == "中国"
        assert known.region == "US"
        assert asked == ["8.8.8.1"]

    def test_no_pending_skips_requests(self, monkeypatch):
        async def fake_batch(ips, session, *, timeout):  # pragma: no cover
            raise AssertionError("不应发起请求")

        monkeypatch.setattr(region, "_batch", fake_batch)
        assert run(region.lookup_many([("8.8.8.1", Proxy("8.8.8.1:80", region="日本"))], None)) == 0

    def test_failed_lookup_keeps_empty_region(self, monkeypatch):
        async def fake_batch(ips, session, *, timeout):
            return {}

        async def fake_lookup(ip, session, **_kwargs):
            return ""

        monkeypatch.setattr(region, "_batch", fake_batch)
        monkeypatch.setattr(region, "lookup", fake_lookup)
        proxy = Proxy("8.8.8.1:80")
        run(region.lookup_many([("8.8.8.1", proxy)], None))
        assert proxy.region == ""

    def test_same_ip_queried_once(self, monkeypatch):
        """同一 IP 的不同端口是同一个出口，只应查一次。"""
        batches: list[list[str]] = []

        async def fake_batch(ips, session, *, timeout):
            batches.append(list(ips))
            return dict.fromkeys(ips, "HK")

        monkeypatch.setattr(region, "_batch", fake_batch)
        proxies = [Proxy(f"8.8.8.1:{port}") for port in (80, 8080, 3128)]

        run(region.lookup_many([("8.8.8.1", p) for p in proxies], None))

        assert batches == [["8.8.8.1"]]
        assert [p.region for p in proxies] == ["中国香港"] * 3

    def test_large_ip_set_chunked(self, monkeypatch):
        """超过 100 个 IP 时要分批，不能一次塞给接口。"""
        lengths: list[int] = []

        async def fake_batch(ips, session, *, timeout):
            lengths.append(len(ips))
            return dict.fromkeys(ips, "US")

        monkeypatch.setattr(region, "_batch", fake_batch)
        proxies = [Proxy(f"10.0.{i // 250}.{i % 250}:80") for i in range(250)]
        items = [(p.ip, p) for p in proxies]

        run(region.lookup_many(items, None))

        assert sorted(lengths) == [50, 100, 100]
        assert all(p.region == "美国" for p in proxies)
