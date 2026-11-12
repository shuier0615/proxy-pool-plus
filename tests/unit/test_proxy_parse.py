"""代理字符串解析测试。"""

from __future__ import annotations

import pytest

from utils.proxy_parse import is_public_ip, normalize, parse_proxies, parse_proxy


class TestParseProxies:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1.2.3.4:8080", ["1.2.3.4:8080"]),
            ("1.2.3.4 8080", ["1.2.3.4:8080"]),
            ("1.2.3.4,8080", ["1.2.3.4:8080"]),
            ("8.219.97.248:80:Singapore", ["8.219.97.248:80"]),
            ("user:pwd@1.2.3.4:3128", ["user:pwd@1.2.3.4:3128"]),
        ],
    )
    def test_formats(self, text, expected):
        assert parse_proxies(text) == expected

    def test_multiple_and_dedup(self):
        text = "1.2.3.4:8080\n5.6.7.8:3128\n1.2.3.4:8080"
        assert parse_proxies(text) == ["1.2.3.4:8080", "5.6.7.8:3128"]

    def test_filters_private_and_invalid(self):
        text = "10.0.0.1:80\n127.0.0.1:80\n192.168.1.1:8080\n8.8.8.8:99999\n1.1.1.1:80"
        assert parse_proxies(text) == ["1.1.1.1:80"]

    def test_limit(self):
        text = " ".join(f"8.8.8.{i}:80" for i in range(1, 21))
        assert len(parse_proxies(text, limit=5)) == 5

    def test_empty(self):
        assert parse_proxies("") == []
        assert parse_proxies(None) == []
        assert parse_proxies("no proxy here") == []

    def test_html_fragment_needs_dedicated_parser(self):
        """纯文本解析器不跨标签匹配，HTML 页面由各源自己的 xpath/正则处理。"""
        text = "<td>8.8.8.8</td><td>8080</td>"
        assert parse_proxies(text) == []


class TestHelpers:
    def test_is_public_ip(self):
        assert is_public_ip("8.8.8.8")
        assert not is_public_ip("192.168.0.1")
        assert not is_public_ip("224.0.0.1")
        assert not is_public_ip("not-an-ip")

    def test_normalize(self):
        assert normalize("8.8.8.8", "80") == "8.8.8.8:80"
        assert normalize("8.8.8.8", 80, "u:p") == "u:p@8.8.8.8:80"
        assert normalize("8.8.8.8", "0") is None
        assert normalize("8.8.8.8", "abc") is None
        assert normalize("10.0.0.1", "80") is None

    def test_parse_proxy(self):
        assert parse_proxy("8.8.8.8:8080") == ("8.8.8.8", 8080)
        assert parse_proxy("u:p@8.8.8.8:8080") == ("8.8.8.8", 8080)
        assert parse_proxy("8.8.8.8") is None
        assert parse_proxy("10.0.0.1:80") is None

    def test_parse_proxy_rejects_out_of_range_port(self):
        """端口必须落在 1~65535，否则会白占一个校验配额。"""
        assert parse_proxy("8.8.8.8:0") is None
        assert parse_proxy("8.8.8.8:65536") is None
        assert parse_proxy("8.8.8.8:99999") is None
        assert parse_proxy("8.8.8.8:65535") == ("8.8.8.8", 65535)
        assert parse_proxy("8.8.8.8:1") == ("8.8.8.8", 1)

    def test_parse_proxy_strips_whitespace(self):
        """源返回的脏数据常带空格/换行，parse_proxy 自身要能容忍。"""
        assert parse_proxy(" 8.8.8.8:8080 ") == ("8.8.8.8", 8080)
        assert parse_proxy("8.8.8.8:8080\n") == ("8.8.8.8", 8080)
        assert parse_proxy("\t8.8.8.8:8080\r\n") == ("8.8.8.8", 8080)
