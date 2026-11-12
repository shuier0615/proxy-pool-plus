"""校验器测试（探测函数被替换，不触网）。"""

from __future__ import annotations

import asyncio
import time

from checker.validator import Validator
from config import get_settings
from models import Proxy


def run(coro):
    return asyncio.run(coro)


class TestFormat:
    def test_format_ok(self):
        validator = Validator(get_settings())
        assert validator.format_ok("8.8.8.8:80")
        assert not validator.format_ok("10.0.0.1:80")
        assert not validator.format_ok("8.8.8.8")


class TestValidate:
    def test_http_and_https_pass(self, monkeypatch):
        async def fake_probe(self, session, url, proxy_url):
            return True

        monkeypatch.setattr(Validator, "_probe", fake_probe)
        proxy = run(Validator(get_settings()).validate(Proxy("8.8.8.8:80"), None))
        assert proxy.last_status is True
        assert proxy.https is True
        assert proxy.check_count == 1

    def test_http_only(self, monkeypatch):
        settings = get_settings()
        calls = {"n": 0}

        async def fake_probe(self, session, url, proxy_url):
            calls["n"] += 1
            return calls["n"] == 1  # 第一次 http 成功，第二次 https 失败

        monkeypatch.setattr(Validator, "_probe", fake_probe)
        proxy = run(Validator(settings).validate(Proxy("8.8.8.8:80"), None))
        assert proxy.last_status is True
        assert proxy.https is False

    def test_failure_increments(self, monkeypatch):
        async def fake_probe(self, session, url, proxy_url):
            return False

        monkeypatch.setattr(Validator, "_probe", fake_probe)
        proxy = run(Validator(get_settings()).validate(Proxy("8.8.8.8:80"), None))
        assert proxy.last_status is False
        assert proxy.fail_count == 1
        assert proxy.check_count == 1


class TestHardTimeout:
    """单代理硬超时：aiohttp 的 ClientTimeout 在 SOCKS 通道上会失效
    （实测配置 10s 的探测跑到 60s），必须由外层 asyncio.timeout 兜住。"""

    def test_slow_validation_is_cut_off(self, monkeypatch):
        async def slow(self, proxy, session):
            await asyncio.sleep(5)
            proxy.mark_success(True)
            return proxy

        monkeypatch.setattr(Validator, "_validate", slow)
        validator = Validator(get_settings())
        validator.hard_timeout = 0.1

        started = time.perf_counter()
        proxy = run(validator.validate(Proxy("8.8.8.8:80"), None))
        elapsed = time.perf_counter() - started

        assert elapsed < 1.0
        assert proxy.last_status is False
        assert proxy.fail_count == 1

    def test_default_hard_timeout_covers_two_probes(self):
        settings = get_settings()
        validator = Validator(settings)
        assert validator.hard_timeout >= settings.verify_timeout * 2


class TestValidateMany:
    def test_filters_bad_format(self, monkeypatch):
        async def fake_probe(self, session, url, proxy_url):
            return True

        monkeypatch.setattr(Validator, "_probe", fake_probe)
        proxies = [Proxy("8.8.8.8:80"), Proxy("127.0.0.1:80")]
        result = run(Validator(get_settings()).validate_many(proxies))
        assert len(result) == 1
        assert result[0].proxy == "8.8.8.8:80"

    def test_empty(self):
        assert run(Validator(get_settings()).validate_many([])) == []

    def test_handles_exception(self, monkeypatch):
        async def boom(self, proxy, session):
            raise RuntimeError("boom")

        monkeypatch.setattr(Validator, "validate", boom)
        result = run(Validator(get_settings()).validate_many([Proxy("8.8.8.8:80")]))
        assert len(result) == 1
        assert result[0].last_status is False
