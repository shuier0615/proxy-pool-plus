"""代理源基类与注册表测试。"""

from __future__ import annotations

import asyncio

import pytest

from fetcher.base import BaseFetcher, TextListFetcher, unique
from fetcher.registry import discover_sources, reset_cache, source_names


def run(coro):
    return asyncio.run(coro)


class TestUnique:
    def test_dedup_keep_order(self):
        assert unique(["a", "b", "a", "", None, "c"]) == ["a", "b", "c"]


class _SampleSource(TextListFetcher):
    name = "sample"
    url = "https://example.com"
    urls = ("https://example.com/a", "https://example.com/b")


class TestTextListFetcher:
    def test_fetch_merges_urls(self, monkeypatch):
        async def fake_get_text(self, session, url, **kwargs):
            return "8.8.8.8:80\n1.1.1.1:8080" if url.endswith("/a") else "8.8.8.8:80\n9.9.9.9:3128"

        monkeypatch.setattr(TextListFetcher, "get_text", fake_get_text)
        result = run(_SampleSource().fetch(session=None))
        assert result == ["8.8.8.8:80", "1.1.1.1:8080", "9.9.9.9:3128"]

    def test_parse_html_invalid(self):
        assert BaseFetcher.parse_html("") is None
        tree = BaseFetcher.parse_html("<table><tr><td>1</td></tr></table>")
        assert tree is not None
        assert tree.xpath("//td/text()") == ["1"]

    def test_partial_url_failure_keeps_other_results(self, monkeypatch):
        """多地址源中某个地址失败，不应丢掉其他地址已解析到的代理。"""

        async def fake_get_text(self, session, url, **kwargs):
            if url.endswith("/a"):
                raise RuntimeError("mirror down")
            return "9.9.9.9:3128"

        monkeypatch.setattr(TextListFetcher, "get_text", fake_get_text)
        assert run(_SampleSource().fetch(session=None)) == ["9.9.9.9:3128"]

    def test_base_fetcher_is_abstract(self):
        with pytest.raises(TypeError):
            BaseFetcher()  # type: ignore[abstract]


class TestRegistry:
    def test_discovers_sources(self):
        reset_cache()
        classes = discover_sources()
        assert len(classes) >= 20
        names = {cls.name for cls in classes}
        assert {"ip89", "monosans", "thespeedx", "geonode"} <= names
        # 不应包含抽象基类
        assert BaseFetcher not in classes

    def test_exclude_by_name_and_class(self):
        reset_cache()
        names = {cls.name for cls in discover_sources({"ip89", "MonosansFetcher"})}
        assert "ip89" not in names
        assert "monosans" not in names
        assert "thespeedx" in names

    def test_unique_names(self):
        reset_cache()
        names = source_names()
        assert len(names) == len(set(names))
