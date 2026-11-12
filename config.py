"""运行时配置。

优先级：环境变量 > setting.py 缺省值。

`get_settings()` 带缓存，进程内只解析一次；测试中可使用
`get_settings.cache_clear()` 重新读取环境变量。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

import setting


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str]) -> frozenset[str]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return frozenset(default)
    return frozenset(item.strip() for item in raw.replace(";", ",").split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    """不可变配置对象。"""

    host: str = setting.HOST
    port: int = setting.PORT
    db_conn: str = setting.DB_CONN
    table_name: str = setting.TABLE_NAME

    http_url: str = setting.HTTP_URL
    https_url: str = setting.HTTPS_URL
    verify_timeout: float = setting.VERIFY_TIMEOUT
    max_fail_count: int = setting.MAX_FAIL_COUNT
    get_verify: bool = setting.GET_VERIFY
    get_verify_attempts: int = setting.GET_VERIFY_ATTEMPTS
    get_verify_total: float = setting.GET_VERIFY_TOTAL
    pool_size_min: int = setting.POOL_SIZE_MIN
    proxy_region: bool = setting.PROXY_REGION
    proxy_anonymity: bool = setting.PROXY_ANONYMITY
    meta_concurrency: int = setting.META_CONCURRENCY
    meta_max_per_round: int = setting.META_MAX_PER_ROUND
    anonymity_url: str = setting.ANONYMITY_URL
    max_per_source: int = setting.MAX_PROXIES_PER_SOURCE
    max_new: int = setting.MAX_NEW_PROXIES
    socks_max_new: int = setting.SOCKS_MAX_NEW_PROXIES
    source_weighting: bool = setting.SOURCE_WEIGHTING
    source_weight_prior: float = setting.SOURCE_WEIGHT_PRIOR
    source_weight_strength: int = setting.SOURCE_WEIGHT_STRENGTH

    fetch_concurrency: int = setting.FETCH_CONCURRENCY
    check_concurrency: int = setting.CHECK_CONCURRENCY
    check_max_per_round: int = setting.CHECK_MAX_PER_ROUND
    source_timeout: float = setting.SOURCE_TIMEOUT

    fetch_interval: int = setting.FETCH_INTERVAL_MINUTES
    check_interval: int = setting.CHECK_INTERVAL_MINUTES
    timezone: str = setting.TIMEZONE

    fetcher_exclude: frozenset[str] = field(default_factory=frozenset)
    log_level: str = setting.LOG_LEVEL

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            host=_env_str("HOST", setting.HOST),
            port=_env_int("PORT", setting.PORT),
            db_conn=_env_str("DB_CONN", setting.DB_CONN),
            table_name=_env_str("TABLE_NAME", setting.TABLE_NAME),
            http_url=_env_str("HTTP_URL", setting.HTTP_URL),
            https_url=_env_str("HTTPS_URL", setting.HTTPS_URL),
            verify_timeout=_env_float("VERIFY_TIMEOUT", setting.VERIFY_TIMEOUT),
            max_fail_count=_env_int("MAX_FAIL_COUNT", setting.MAX_FAIL_COUNT),
            get_verify=_env_bool("GET_VERIFY", setting.GET_VERIFY),
            get_verify_attempts=_env_int("GET_VERIFY_ATTEMPTS", setting.GET_VERIFY_ATTEMPTS),
            get_verify_total=_env_float("GET_VERIFY_TOTAL", setting.GET_VERIFY_TOTAL),
            pool_size_min=_env_int("POOL_SIZE_MIN", setting.POOL_SIZE_MIN),
            proxy_region=_env_bool("PROXY_REGION", setting.PROXY_REGION),
            proxy_anonymity=_env_bool("PROXY_ANONYMITY", setting.PROXY_ANONYMITY),
            meta_concurrency=_env_int("META_CONCURRENCY", setting.META_CONCURRENCY),
            meta_max_per_round=_env_int("META_MAX_PER_ROUND", setting.META_MAX_PER_ROUND),
            anonymity_url=_env_str("ANONYMITY_URL", setting.ANONYMITY_URL),
            max_per_source=_env_int("MAX_PROXIES_PER_SOURCE", setting.MAX_PROXIES_PER_SOURCE),
            max_new=_env_int("MAX_NEW_PROXIES", setting.MAX_NEW_PROXIES),
            socks_max_new=_env_int("SOCKS_MAX_NEW_PROXIES", setting.SOCKS_MAX_NEW_PROXIES),
            source_weighting=_env_bool("SOURCE_WEIGHTING", setting.SOURCE_WEIGHTING),
            source_weight_prior=_env_float("SOURCE_WEIGHT_PRIOR", setting.SOURCE_WEIGHT_PRIOR),
            source_weight_strength=_env_int("SOURCE_WEIGHT_STRENGTH", setting.SOURCE_WEIGHT_STRENGTH),
            fetch_concurrency=_env_int("FETCH_CONCURRENCY", setting.FETCH_CONCURRENCY),
            check_concurrency=_env_int("CHECK_CONCURRENCY", setting.CHECK_CONCURRENCY),
            check_max_per_round=_env_int("CHECK_MAX_PER_ROUND", setting.CHECK_MAX_PER_ROUND),
            source_timeout=_env_float("SOURCE_TIMEOUT", setting.SOURCE_TIMEOUT),
            fetch_interval=_env_int("FETCH_INTERVAL_MINUTES", setting.FETCH_INTERVAL_MINUTES),
            check_interval=_env_int("CHECK_INTERVAL_MINUTES", setting.CHECK_INTERVAL_MINUTES),
            timezone=_env_str("TIMEZONE", setting.TIMEZONE),
            fetcher_exclude=_env_list("PROXY_FETCHER_EXCLUDE", setting.PROXY_FETCHER_EXCLUDE),
            log_level=_env_str("LOG_LEVEL", setting.LOG_LEVEL).upper(),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回进程级配置单例。"""
    return Settings.from_env()
