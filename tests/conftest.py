"""共享 fixtures。"""

from __future__ import annotations

import sys
from pathlib import Path

import fakeredis
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import get_settings  # noqa: E402
from models import Proxy  # noqa: E402
from storage.redis_store import RedisStore  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_settings():
    """每个用例前后清空配置缓存，保证环境变量覆盖生效且互不影响。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _offline_metadata(monkeypatch):
    """默认关掉元数据补全与出仓检测：单测不应触网（ip-api / httpbin 都是外部服务）。

    需要验证补全/出仓检测逻辑的用例自行 ``dataclasses.replace(...)`` 或设环境变量
    并打桩对应的网络函数。
    """
    monkeypatch.setenv("PROXY_REGION", "0")
    monkeypatch.setenv("PROXY_ANONYMITY", "0")
    monkeypatch.setenv("GET_VERIFY", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def store(fake_redis) -> RedisStore:
    return RedisStore("redis://localhost:6379/0", "test_proxy", client=fake_redis)


@pytest.fixture
def proxy_obj() -> Proxy:
    return Proxy("1.2.3.4:8080", sources={"test"})


@pytest.fixture
def https_proxy_obj() -> Proxy:
    return Proxy("5.6.7.8:443", sources={"test"}, https=True)


@pytest.fixture
def api_service(store):
    from service import ProxyPoolService

    return ProxyPoolService(settings=get_settings(), store=store)


@pytest.fixture
def app(api_service):
    from api.app import create_app

    application = create_app(api_service)
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(app):
    return app.test_client()
