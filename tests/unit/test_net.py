"""HTTP 工具与 GitHub 镜像回退测试（不触网）。"""

from __future__ import annotations

import asyncio

import pytest

from utils import net

RAW = "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"


def run(coro):
    return asyncio.run(coro)


class TestGithubMirrors:
    def test_raw_url_expands_to_mirrors(self):
        urls = net.github_mirrors(RAW)
        assert urls[0] == f"https://ghproxy.net/{RAW}"
        assert urls[1] == f"https://gh-proxy.com/{RAW}"
        assert "https://fastly.jsdelivr.net/gh/TheSpeedX/PROXY-List@master/http.txt" in urls
        # 原始地址始终放在最后兜底
        assert urls[-1] == RAW

    def test_non_github_url_untouched(self):
        url = "https://api.proxyscrape.com/v4/free-proxy-list/get?x=1"
        assert net.github_mirrors(url) == [url]

    def test_short_path_skips_jsdelivr(self):
        urls = net.github_mirrors("https://raw.githubusercontent.com/a/b")
        assert not any("jsdelivr" in u for u in urls)
        assert urls[-1].endswith("/a/b")


class TestFetchTextMirrors:
    def test_falls_through_to_working_mirror(self, monkeypatch):
        calls: list[str] = []

        async def fake_fetch_text(session, url, **kwargs):
            calls.append(url)
            return "data" if "fastly" in url else ""

        monkeypatch.setattr(net, "fetch_text", fake_fetch_text)
        assert run(net.fetch_text_mirrors(None, RAW, timeout=5)) == "data"
        assert len(calls) == 3
        assert "fastly" in calls[-1]

    def test_all_mirrors_fail_returns_empty(self, monkeypatch):
        async def fake_fetch_text(session, url, **kwargs):
            return ""

        monkeypatch.setattr(net, "fetch_text", fake_fetch_text)
        assert run(net.fetch_text_mirrors(None, RAW, timeout=5)) == ""

    def test_non_github_single_attempt(self, monkeypatch):
        calls: list[str] = []

        async def fake_fetch_text(session, url, **kwargs):
            calls.append(url)
            return "ok"

        monkeypatch.setattr(net, "fetch_text", fake_fetch_text)
        assert run(net.fetch_text_mirrors(None, "https://example.com/x", timeout=5)) == "ok"
        assert calls == ["https://example.com/x"]

    def test_deadline_stops_retry(self, monkeypatch):
        """总预算耗尽后不再尝试后续镜像。"""
        calls: list[str] = []

        async def fake_fetch_text(session, url, **kwargs):
            calls.append(url)
            return ""

        monkeypatch.setattr(net, "fetch_text", fake_fetch_text)
        monkeypatch.setattr(net, "github_mirrors", lambda url: [url + f"?i={i}" for i in range(50)])
        run(net.fetch_text_mirrors(None, RAW, timeout=0.05))
        assert len(calls) <= 2


@pytest.mark.parametrize(
    ("charset", "expect"),
    [(None, "中文"), ("utf-8", "中文")],
)
def test_decode(charset, expect):
    assert net._decode("中文".encode(), charset) == expect
