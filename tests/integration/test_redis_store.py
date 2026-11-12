"""Redis 集成测试（需要真实 Redis）。

REDIS_URL=redis://127.0.0.1:6379/0 pytest tests/integration -m integration
"""

from __future__ import annotations

import os
import uuid

import pytest

from models import Proxy
from storage.redis_store import RedisStore

pytestmark = pytest.mark.integration

REDIS_URL = os.getenv("REDIS_URL", "")


@pytest.fixture
def live_store():
    if not REDIS_URL:
        pytest.skip("未设置 REDIS_URL，跳过集成测试")
    table = f"pytest_proxy_{uuid.uuid4().hex[:8]}"
    store = RedisStore(REDIS_URL, table)
    if not store.ping():
        pytest.skip("Redis 不可用")
    yield store
    store.clear()


class TestLiveStore:
    def test_roundtrip(self, live_store):
        live_store.put(Proxy("8.8.8.8:8080", https=True, sources={"it"}))
        assert live_store.exists("8.8.8.8:8080")
        assert Proxy.from_json(live_store.get()).source == "it"

    def test_count_and_all(self, live_store):
        live_store.put_many([Proxy("8.8.8.1:80"), Proxy("8.8.8.2:443", https=True)])
        stats = live_store.count()
        assert (stats["total"], stats["https"], stats["http"]) == (2, 1, 1)
        assert stats["socks5"] == 0
        assert stats["grand_total"] == 2
        assert len(live_store.all()) == 2
        assert len(live_store.all(https=True)) == 1

    def test_reindex(self, live_store):
        live_store.put(Proxy("8.8.8.1:80", https=True))
        result = live_store.reindex()
        assert result["total"] == 1
        assert result["https"] == 1
        assert result["http_https"] == 1
        assert result["socks5"] == 0

    def test_socks_isolated_from_http(self, live_store):
        """同一 host:port 可同时作为 HTTP 与 SOCKS5 存在，互不覆盖。"""
        live_store.put(Proxy("8.8.8.9:1080"))
        live_store.put(Proxy("8.8.8.9:1080", scheme="socks5", https=True))

        assert live_store.exists("8.8.8.9:1080", "http")
        assert live_store.exists("8.8.8.9:1080", "socks5")
        assert Proxy.from_json(live_store.get(scheme="socks5")).scheme == "socks5"
        assert Proxy.from_json(live_store.get(scheme="http")).scheme == "http"

        stats = live_store.count()
        assert stats["total"] == 1
        assert stats["socks5"] == 1
        assert stats["grand_total"] == 2
        assert len(live_store.all(scheme="all")) == 2

        assert live_store.delete_any("8.8.8.9:1080") == ["http", "socks5"]
        assert live_store.count()["grand_total"] == 0
