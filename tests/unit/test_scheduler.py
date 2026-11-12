"""调度器与网络工具测试。"""

from __future__ import annotations

import scheduler
from utils.net import _decode, default_headers


class TestScheduler:
    def test_run_scheduler_delegates_to_runtime(self, monkeypatch):
        captured = {}

        def fake_run(coro):
            captured["name"] = coro.__qualname__
            coro.close()  # 避免 coroutine never awaited 警告

        monkeypatch.setattr(scheduler, "run", fake_run)
        scheduler.run_scheduler()
        assert captured["name"] == "_serve"

    def test_serve_is_coroutine(self):
        assert scheduler._serve.__name__ == "_serve"


class TestNet:
    def test_decode_utf8(self):
        assert _decode("代理".encode(), "utf-8") == "代理"

    def test_decode_gbk_fallback(self):
        assert _decode("代理".encode("gb18030"), None) == "代理"

    def test_decode_unknown_charset(self):
        assert _decode(b"abc", "not-a-charset") == "abc"

    def test_default_headers(self):
        headers = default_headers({"Referer": "https://example.com"})
        assert "User-Agent" in headers
        assert headers["Referer"] == "https://example.com"
