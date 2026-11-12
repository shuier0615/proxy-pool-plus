"""CLI 测试。"""

from __future__ import annotations

from click.testing import CliRunner

import proxyPool
import setting
from proxyPool import cli


class TestBasic:
    def test_version(self):
        result = CliRunner().invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert setting.VERSION in result.output

    def test_help_lists_commands(self):
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0
        for command in ("server", "schedule", "fetch", "check", "fetcher", "stats", "reindex", "clear", "import"):
            assert command in result.output


class TestFetcherList:
    def test_lists_sources(self):
        result = CliRunner().invoke(cli, ["fetcher"])
        assert result.exit_code == 0
        assert "ip89" in result.output
        assert "启用的代理源" in result.output

    def test_exclude_via_env(self, monkeypatch):
        monkeypatch.setenv("PROXY_FETCHER_EXCLUDE", "ip89")
        from config import get_settings

        get_settings.cache_clear()
        result = CliRunner().invoke(cli, ["fetcher"])
        assert result.exit_code == 0
        assert "已排除: ip89" in result.output


class TestClear:
    def test_requires_confirmation(self):
        result = CliRunner().invoke(cli, ["clear"])
        assert result.exit_code != 0
        assert "--yes" in result.output


class TestImport:
    def test_import_file(self, tmp_path, monkeypatch):
        path = tmp_path / "proxies.txt"
        path.write_text("8.8.8.1:80\n8.8.8.2:8080\nbad-line\n", encoding="utf-8")

        seeded = {}

        class FakeService:
            def seed(self, proxies, source="manual"):
                seeded["proxies"] = list(proxies)
                seeded["source"] = source
                return len(seeded["proxies"])

        monkeypatch.setattr(proxyPool, "_service", lambda conf=None: FakeService())
        result = CliRunner().invoke(cli, ["import", str(path), "--source", "file"])
        assert result.exit_code == 0
        assert seeded["proxies"] == ["8.8.8.1:80", "8.8.8.2:8080"]
        assert seeded["source"] == "file"

    def test_import_empty_file(self, tmp_path):
        path = tmp_path / "empty.txt"
        path.write_text("nothing here", encoding="utf-8")
        result = CliRunner().invoke(cli, ["import", str(path)])
        assert result.exit_code != 0
        assert "未解析到有效代理" in result.output


def test_cli_entry_is_importable():
    assert proxyPool.cli is cli
