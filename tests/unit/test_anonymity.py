"""匿名度判定测试（不触网）。"""

from __future__ import annotations

import asyncio

from checker import anonymity
from models import Proxy


def run(coro):
    return asyncio.run(coro)


class TestClassify:
    def test_forwarded_header_is_transparent(self):
        assert anonymity.classify({"x-forwarded-for": "1.2.3.4"}, "5.6.7.8") == anonymity.TRANSPARENT

    def test_real_ip_header_is_transparent(self):
        assert anonymity.classify({"x-real-ip": "1.2.3.4"}, "5.6.7.8") == anonymity.TRANSPARENT

    def test_via_is_anonymous(self):
        assert anonymity.classify({"via": "1.1 squid"}, "5.6.7.8") == anonymity.ANONYMOUS

    def test_proxy_connection_is_anonymous(self):
        assert anonymity.classify({"proxy-connection": "keep-alive"}, "5.6.7.8") == anonymity.ANONYMOUS

    def test_proxy_ip_leak_is_anonymous(self):
        assert anonymity.classify({"x-note": "5.6.7.8"}, "9.9.9.9", "5.6.7.8") == anonymity.ANONYMOUS

    def test_clean_headers_are_high_anonymous(self):
        headers = {"host": "httpbin.org", "user-agent": "curl/8.0"}
        assert anonymity.classify(headers, "5.6.7.8") == anonymity.HIGH_ANONYMOUS

    def test_multi_hop_origin_is_transparent(self):
        assert anonymity.classify({"host": "h"}, "1.1.1.1, 2.2.2.2") == anonymity.TRANSPARENT

    def test_single_hop_origin_stays_high(self):
        assert anonymity.classify({"host": "h"}, "1.1.1.1") == anonymity.HIGH_ANONYMOUS

    def test_empty_headers(self):
        assert anonymity.classify({}, "1.1.1.1") == ""

    def test_header_keys_case_insensitive(self):
        # httpbin 返回的键大小写混用，_extract 会统一小写
        headers, origin = anonymity._extract({"headers": {"X-Forwarded-For": "1.2.3.4"}, "origin": "5.6.7.8"})
        assert anonymity.classify(headers, origin) == anonymity.TRANSPARENT

    def test_extract_non_dict(self):
        assert anonymity._extract(None) == ({}, "")
        assert anonymity._extract({"origin": "1.1.1.1"}) == ({}, "")


class TestDetectMany:
    def test_fills_probed_value(self, monkeypatch):
        async def fake_http(session, proxy, urls):
            return anonymity.ANONYMOUS

        monkeypatch.setattr(anonymity, "_detect_http", fake_http)
        proxies = [Proxy("1.1.1.1:80"), Proxy("2.2.2.2:80")]

        assert run(anonymity.detect_many(proxies)) == 2
        assert all(p.anonymous == anonymity.ANONYMOUS for p in proxies)

    def test_failure_keeps_empty(self, monkeypatch):
        async def fake_http(session, proxy, urls):
            return ""

        monkeypatch.setattr(anonymity, "_detect_http", fake_http)
        proxy = Proxy("1.1.1.1:80")

        assert run(anonymity.detect_many([proxy])) == 0
        assert proxy.anonymous == ""

    def test_skips_existing_value(self, monkeypatch):
        async def boom(*args, **kwargs):  # pragma: no cover
            raise AssertionError("已有匿名度不应再探测")

        monkeypatch.setattr(anonymity, "_detect_http", boom)
        proxy = Proxy("1.1.1.1:80", anonymous="anonymous")

        assert run(anonymity.detect_many([proxy])) == 0
        assert proxy.anonymous == "anonymous"

    def test_socks_path(self, monkeypatch):
        async def fake_socks(proxy, urls):
            return anonymity.HIGH_ANONYMOUS

        monkeypatch.setattr(anonymity, "_detect_socks", fake_socks)
        proxy = Proxy("9.9.9.9:1080", scheme="socks5")

        assert run(anonymity.detect_many([proxy])) == 1
        assert proxy.anonymous == anonymity.HIGH_ANONYMOUS

    def test_odd_exception_does_not_break_batch(self, monkeypatch):
        """python-socks 断连时抛 IncompleteReadError（EOFError 子类）。

        它既不是 ClientError 也不是 OSError，早期版本会穿透 TaskGroup 让整批探测
        直接崩掉，连已经成功的结果都拿不回来。
        """

        async def boom(session, proxy, urls):
            raise asyncio.IncompleteReadError(b"", 2)

        monkeypatch.setattr(anonymity, "_detect_http", boom)
        proxies = [Proxy("1.1.1.1:80"), Proxy("2.2.2.2:80")]

        assert run(anonymity.detect_many(proxies)) == 0
        assert all(p.anonymous == "" for p in proxies)
