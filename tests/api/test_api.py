"""HTTP API 测试。"""

from __future__ import annotations

from models import Proxy


def seed(store, *proxies: Proxy) -> None:
    for proxy in proxies:
        store.put(proxy)


class TestIndex:
    def test_index(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["version"]
        assert {item["url"] for item in data["url"]} >= {"/get", "/pop", "/all", "/count", "/delete"}


class TestGet:
    def test_get_proxy(self, client, store):
        seed(store, Proxy("1.2.3.4:8080", sources={"t"}, https=True))
        data = client.get("/get").get_json()
        assert data["proxy"] == "1.2.3.4:8080"
        assert data["source"] == "t"
        assert data["https"] is True

    def test_get_https_only(self, client, store):
        seed(store, Proxy("1.2.3.4:8080", https=False))
        assert client.get("/get?type=https").status_code == 404
        assert client.get("/get?type=https").get_json()["code"] == 0

    def test_get_text_format(self, client, store):
        seed(store, Proxy("1.2.3.4:8080"))
        resp = client.get("/get?format=text")
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == "1.2.3.4:8080"

    def test_get_empty(self, client):
        resp = client.get("/get")
        assert resp.status_code == 404
        assert resp.get_json() == {"code": 0, "src": "no proxy"}

    def test_get_metadata_defaults(self, client, store):
        """存储里没有地域/匿名度时输出默认值，而不是空串。"""
        seed(store, Proxy("1.2.3.4:8080"))
        data = client.get("/get").get_json()
        assert data["region"] == "未知"
        assert data["anonymous"] == "unknown"

    def test_get_keeps_known_metadata(self, client, store):
        seed(store, Proxy("1.2.3.4:8080", region="中国", anonymous="high_anonymous"))
        data = client.get("/get").get_json()
        assert data["region"] == "中国"
        assert data["anonymous"] == "high_anonymous"


class TestPop:
    def test_pop_removes(self, client, store):
        seed(store, Proxy("1.2.3.4:8080"))
        assert client.get("/pop").status_code == 200
        assert client.get("/pop").status_code == 404
        assert store.count()["total"] == 0

    def test_pop_empty(self, client):
        resp = client.get("/pop")
        assert resp.status_code == 404


class TestAll:
    def test_all(self, client, store):
        seed(store, Proxy("1.2.3.4:8080"), Proxy("5.6.7.8:443", https=True))
        data = client.get("/all").get_json()
        assert len(data) == 2

    def test_all_https(self, client, store):
        seed(store, Proxy("1.2.3.4:8080"), Proxy("5.6.7.8:443", https=True))
        data = client.get("/all?type=https").get_json()
        assert [d["proxy"] for d in data] == ["5.6.7.8:443"]

    def test_all_limit(self, client, store):
        seed(store, Proxy("1.2.3.4:8080"), Proxy("5.6.7.8:443"))
        assert len(client.get("/all?limit=1").get_json()) == 1

    def test_all_empty(self, client):
        assert client.get("/all").get_json() == []

    def test_all_metadata_defaults(self, client, store):
        seed(store, Proxy("1.2.3.4:8080"))
        data = client.get("/all").get_json()
        assert data[0]["region"] == "未知"
        assert data[0]["anonymous"] == "unknown"


class TestCount:
    def test_count(self, client, store):
        seed(store, Proxy("1.2.3.4:8080"), Proxy("5.6.7.8:443", https=True))
        data = client.get("/count").get_json()
        assert data["count"] == 2
        assert data["https"] == 1
        assert data["http_type"] == {"http": 1, "https": 1}
        assert "source" not in data

    def test_count_detail(self, client, store):
        seed(store, Proxy("1.2.3.4:8080", sources={"a"}))
        store.record_source_outcomes({"a": 10}, {"a": 4})
        data = client.get("/count?detail=1").get_json()
        assert data["source"] == {"a": 1}
        assert data["source_history"]["a"]["checked"] == 10
        assert data["source_history"]["a"]["valid"] == 4
        assert 0 < data["source_history"]["a"]["rate"] < 1

    def test_count_detail_omitted_by_default(self, client, store):
        seed(store, Proxy("1.2.3.4:8080", sources={"a"}))
        store.record_source_outcomes({"a": 10}, {"a": 4})
        data = client.get("/count").get_json()
        assert "source_history" not in data

    def test_count_empty(self, client):
        assert client.get("/count").get_json()["count"] == 0


class TestDelete:
    def test_delete(self, client, store):
        seed(store, Proxy("1.2.3.4:8080"))
        data = client.get("/delete?proxy=1.2.3.4:8080").get_json()
        assert data == {"code": 0, "src": True, "scheme": "http"}
        assert store.count()["total"] == 0

    def test_delete_missing_param(self, client):
        resp = client.get("/delete")
        assert resp.status_code == 400
        assert resp.get_json()["code"] == 1


class TestMisc:
    def test_sources(self, client):
        data = client.get("/sources").get_json()
        assert isinstance(data, list) and data
        assert {"name", "url", "protocols"} <= set(data[0])

    def test_health(self, client):
        data = client.get("/health").get_json()
        assert data["redis"] is True
        assert data["status"] == "ok"

    def test_redis_error_returns_503(self, client, api_service, monkeypatch):
        from redis.exceptions import ConnectionError as RedisConnectionError

        def boom(*args, **kwargs):
            raise RedisConnectionError("connection refused")

        monkeypatch.setattr(api_service.store, "get", boom)
        resp = client.get("/get")
        assert resp.status_code == 503
        assert resp.get_json() == {"code": 503, "msg": "redis unavailable"}

    def test_404_json(self, client):
        resp = client.get("/not-exists")
        assert resp.status_code == 404
        assert resp.get_json()["code"] == 404

    def test_strict_slashes_disabled(self, client):
        # 尾部斜杠同样命中 /get 路由（返回业务 code 0，而非 404 处理器的 code 404）
        resp = client.get("/get/")
        assert resp.status_code == 404
        assert resp.get_json()["code"] == 0


class TestSocksEndpoints:
    def test_get_socks5_only(self, client, store):
        seed(store, Proxy("1.2.3.4:8080"))
        seed(store, Proxy("9.9.9.9:1080", scheme="socks5"))
        data = client.get("/get?type=socks5").get_json()
        assert data["proxy"] == "9.9.9.9:1080"
        assert data["scheme"] == "socks5"

    def test_get_socks5_empty(self, client, store):
        seed(store, Proxy("1.2.3.4:8080"))
        assert client.get("/get?type=socks5").status_code == 404

    def test_count_reports_socks(self, client, store):
        seed(store, Proxy("1.2.3.4:8080", https=True))
        seed(store, Proxy("9.9.9.9:1080", scheme="socks5"))
        data = client.get("/count").get_json()
        assert data["count"] == 1
        assert data["https"] == 1
        assert data["socks5"] == 1
        assert data["total"] == 2

    def test_all_type_all(self, client, store):
        seed(store, Proxy("1.2.3.4:8080"))
        seed(store, Proxy("9.9.9.9:1080", scheme="socks5"))
        assert len(client.get("/all?type=all").get_json()) == 2

    def test_delete_with_type(self, client, store):
        seed(store, Proxy("1.1.1.1:1080"))
        seed(store, Proxy("1.1.1.1:1080", scheme="socks5"))
        data = client.get("/delete?proxy=1.1.1.1:1080&type=socks5").get_json()
        assert data == {"code": 0, "src": True, "scheme": "socks5"}
        assert store.count()["socks5"] == 0
        assert store.count()["total"] == 1

    def test_delete_any(self, client, store):
        seed(store, Proxy("2.2.2.2:1080", scheme="socks5"))
        data = client.get("/delete?proxy=2.2.2.2:1080").get_json()
        assert data["src"] is True
        assert data["scheme"] == "socks5"


class TestEgressVerify:
    """出仓检测接入 /get 与 /pop（可用才出）。"""

    def _enable(self, api_service, monkeypatch, fail=None):
        import dataclasses

        api_service.settings = dataclasses.replace(api_service.settings, get_verify=True)

        async def fake_validate(proxy, session):
            if proxy.proxy in (fail or set()):
                proxy.mark_failure()
            else:
                proxy.mark_success(True)
            return proxy

        monkeypatch.setattr(api_service.validator, "validate", fake_validate)

    def test_get_returns_verified(self, client, store, api_service, monkeypatch):
        self._enable(api_service, monkeypatch)
        seed(store, Proxy("1.2.3.4:8080", sources={"t"}, https=True))
        data = client.get("/get").get_json()
        assert data["proxy"] == "1.2.3.4:8080"
        assert data["source"] == "t"

    def test_get_all_fail_returns_404(self, client, store, api_service, monkeypatch):
        self._enable(api_service, monkeypatch, fail={"1.2.3.4:8080"})
        seed(store, Proxy("1.2.3.4:8080"))
        resp = client.get("/get")
        assert resp.status_code == 404
        assert resp.get_json()["code"] == 0

    def test_get_verify_bypass(self, client, store, api_service, monkeypatch):
        """verify=0 跳过出仓检测，恢复旧行为。"""
        self._enable(api_service, monkeypatch, fail={"1.2.3.4:8080"})
        seed(store, Proxy("1.2.3.4:8080"))
        assert client.get("/get?verify=0").status_code == 200

    def test_pop_removes_verified(self, client, store, api_service, monkeypatch):
        self._enable(api_service, monkeypatch)
        seed(store, Proxy("1.2.3.4:8080"))
        assert client.get("/pop").status_code == 200
        assert client.get("/pop").status_code == 404
        assert store.count()["total"] == 0

    def test_pop_failed_is_discarded(self, client, store, api_service, monkeypatch):
        self._enable(api_service, monkeypatch, fail={"1.2.3.4:8080"})
        seed(store, Proxy("1.2.3.4:8080"))
        assert client.get("/pop").status_code == 404
        assert store.count()["total"] == 0
