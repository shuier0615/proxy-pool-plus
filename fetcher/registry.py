"""代理源自动发现。"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable
from functools import lru_cache

from fetcher.base import BaseFetcher
from logging_setup import get_logger

logger = get_logger("registry")

_SOURCES_PACKAGE = "fetcher.sources"


@lru_cache(maxsize=1)
def _all_sources() -> tuple[type[BaseFetcher], ...]:
    package = importlib.import_module(_SOURCES_PACKAGE)
    found: dict[str, type[BaseFetcher]] = {}

    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name.startswith("_"):
            continue
        module_name = f"{_SOURCES_PACKAGE}.{module_info.name}"
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # 单个源语法/依赖错误不影响其他源
            logger.warning("加载代理源模块失败 %s: %s", module_name, exc)
            continue

        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseFetcher)
                and obj is not BaseFetcher
                and getattr(obj, "name", "")
            ):
                if obj.name in found and found[obj.name] is not obj:
                    logger.warning("代理源重名：%s（%s 被忽略）", obj.name, obj.__name__)
                    continue
                found[obj.name] = obj

    return tuple(sorted(found.values(), key=lambda cls: cls.name))


def discover_sources(exclude: Iterable[str] = ()) -> list[type[BaseFetcher]]:
    """返回启用且未被排除的代理源类。

    ``exclude`` 可同时匹配类名与 ``name``，兼容旧配置写法。
    """
    excluded = {item.strip() for item in exclude if item and item.strip()}
    return [cls for cls in _all_sources() if cls.enabled and cls.__name__ not in excluded and cls.name not in excluded]


def source_names(exclude: Iterable[str] = ()) -> list[str]:
    return [cls.name for cls in discover_sources(exclude)]


def reset_cache() -> None:
    """清除发现结果（测试或热加载时使用）。"""
    _all_sources.cache_clear()
