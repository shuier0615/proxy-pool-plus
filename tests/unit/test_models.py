"""Proxy 模型测试。"""

from __future__ import annotations

import json

from models import Proxy


class TestSerialization:
    def test_roundtrip(self):
        proxy = Proxy("1.2.3.4:8080", https=True, sources={"a", "b"}, fail_count=2)
        restored = Proxy.from_json(proxy.to_json())
        assert restored.to_dict() == proxy.to_dict()
        assert restored.source == "a/b"

    def test_from_dict_defaults(self):
        proxy = Proxy.from_dict({"proxy": "1.1.1.1:80"})
        assert proxy.https is False
        assert proxy.fail_count == 0
        assert proxy.sources == set()

    def test_to_json_is_utf8_friendly(self):
        proxy = Proxy("1.1.1.1:80", region="中国")
        assert "中国" in json.dumps(proxy.to_dict(), ensure_ascii=False)

    def test_source_property_sorted(self):
        proxy = Proxy("1.1.1.1:80", sources={"z", "a"})
        assert proxy.source == "a/z"

    def test_add_source(self):
        proxy = Proxy("1.1.1.1:80")
        proxy.add_source("x")
        proxy.add_source("x")
        proxy.add_source(None)
        assert proxy.sources == {"x"}


class TestDerived:
    def test_ip_and_url(self):
        proxy = Proxy("1.2.3.4:8080")
        assert proxy.ip == "1.2.3.4"
        assert proxy.as_url == "http://1.2.3.4:8080"

    def test_auth_url(self):
        proxy = Proxy("u:p@1.2.3.4:8080")
        assert proxy.ip == "1.2.3.4"
        assert proxy.as_url == "http://u:p@1.2.3.4:8080"

    def test_mark_success(self):
        proxy = Proxy("1.2.3.4:8080", fail_count=2)
        proxy.mark_success(https=True)
        assert proxy.last_status is True
        assert proxy.https is True
        assert proxy.fail_count == 1
        assert proxy.check_count == 1
        assert proxy.last_time

    def test_mark_failure(self):
        proxy = Proxy("1.2.3.4:8080")
        proxy.mark_failure()
        assert proxy.last_status is False
        assert proxy.fail_count == 1
