"""配置加载测试。"""

from __future__ import annotations

import setting
from config import Settings, get_settings


class TestDefaults:
    def test_defaults_match_setting(self):
        conf = get_settings()
        assert conf.port == setting.PORT
        assert conf.table_name == setting.TABLE_NAME
        assert conf.check_concurrency >= 1
        assert isinstance(conf, Settings)

    def test_singleton_cached(self):
        assert get_settings() is get_settings()

    def test_frozen(self):
        conf = get_settings()
        try:
            conf.port = 1  # type: ignore[misc]
        except AttributeError:
            return
        raise AssertionError("Settings 应为不可变对象")

    def test_fail_tolerance_default(self):
        """默认给一次容错：MAX_FAIL_COUNT=0 会在每轮巡检（2 分钟）误删可用代理。"""
        assert get_settings().max_fail_count >= 1

    def test_source_weighting_default_on(self):
        conf = get_settings()
        assert conf.source_weighting is True
        assert 0 < conf.source_weight_prior < 1
        assert conf.source_weight_strength >= 1


class TestEnvOverride:
    def test_env_wins(self, monkeypatch):
        monkeypatch.setenv("PORT", "6000")
        monkeypatch.setenv("VERIFY_TIMEOUT", "3")
        monkeypatch.setenv("CHECK_CONCURRENCY", "500")
        get_settings.cache_clear()
        conf = get_settings()
        assert conf.port == 6000
        assert conf.verify_timeout == 3.0
        assert conf.check_concurrency == 500

    def test_invalid_int_falls_back(self, monkeypatch):
        monkeypatch.setenv("PORT", "not-a-number")
        get_settings.cache_clear()
        assert get_settings().port == setting.PORT

    def test_bool_flag(self, monkeypatch):
        monkeypatch.setenv("PROXY_REGION", "yes")
        get_settings.cache_clear()
        assert get_settings().proxy_region is True

    def test_list_flag(self, monkeypatch):
        monkeypatch.setenv("PROXY_FETCHER_EXCLUDE", "ip89, kuaidaili;zdaye")
        get_settings.cache_clear()
        assert get_settings().fetcher_exclude == frozenset({"ip89", "kuaidaili", "zdaye"})

    def test_source_weighting_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("SOURCE_WEIGHTING", "off")
        monkeypatch.setenv("SOURCE_WEIGHT_PRIOR", "0.25")
        get_settings.cache_clear()
        conf = get_settings()
        assert conf.source_weighting is False
        assert conf.source_weight_prior == 0.25
