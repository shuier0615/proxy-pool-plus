"""代理对象模型。"""

from __future__ import annotations

import json
import time
from typing import Any, Self


class Proxy:
    """池中的单个代理及其健康度指标。

    使用 ``__slots__`` 降低内存占用：10 万代理时相较普通对象可省约 40% 内存。

    ``scheme`` 区分代理协议：``http``（含 HTTPS CONNECT 隧道）与 ``socks5``。
    存储时两种协议使用独立命名空间，因此 ``proxy`` 字段始终保存裸 ``host:port``。
    """

    __slots__ = (
        "proxy",
        "scheme",
        "https",
        "fail_count",
        "region",
        "anonymous",
        "sources",
        "check_count",
        "last_status",
        "last_time",
    )

    def __init__(
        self,
        proxy: str,
        *,
        scheme: str = "http",
        https: bool = False,
        fail_count: int = 0,
        region: str = "",
        anonymous: str = "",
        sources: frozenset[str] | set[str] | None = None,
        check_count: int = 0,
        last_status: bool = False,
        last_time: str = "",
    ) -> None:
        self.proxy = proxy
        self.scheme = scheme
        self.https = https
        self.fail_count = fail_count
        self.region = region
        self.anonymous = anonymous
        self.sources: set[str] = set(sources or ())
        self.check_count = check_count
        self.last_status = last_status
        self.last_time = last_time

    # ------------------------------------------------------------------ 序列化
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            data.get("proxy", ""),
            scheme=data.get("scheme") or "http",
            https=bool(data.get("https", False)),
            fail_count=int(data.get("fail_count", 0) or 0),
            region=data.get("region", "") or "",
            anonymous=data.get("anonymous", "") or "",
            sources=set(filter(None, str(data.get("source", "")).split("/"))),
            check_count=int(data.get("check_count", 0) or 0),
            last_status=bool(data.get("last_status", False)),
            last_time=data.get("last_time", "") or "",
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> Self:
        return cls.from_dict(json.loads(raw))

    def to_dict(self) -> dict[str, Any]:
        return {
            "proxy": self.proxy,
            "scheme": self.scheme,
            "https": self.https,
            "fail_count": self.fail_count,
            "region": self.region,
            "anonymous": self.anonymous,
            "source": self.source,
            "check_count": self.check_count,
            "last_status": self.last_status,
            "last_time": self.last_time,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    # ------------------------------------------------------------------ 行为
    @property
    def is_socks(self) -> bool:
        return self.scheme == "socks5"

    @property
    def source(self) -> str:
        """来源标识，多个来源以 ``/`` 连接。"""
        return "/".join(sorted(self.sources))

    @property
    def ip(self) -> str:
        host = self.proxy.rsplit("@", 1)[-1]
        return host.rsplit(":", 1)[0]

    @property
    def as_url(self) -> str:
        """转换为 http 客户端可用的代理 URL。"""
        host = self.proxy.rsplit("@", 1)[-1]
        auth = self.proxy[: -len(host)] if "@" in self.proxy else ""
        return f"{self.scheme}://{auth}{host}"

    def add_source(self, name: str | None) -> None:
        if name:
            self.sources.add(name)

    def mark_success(self, https: bool) -> None:
        self.check_count += 1
        self.last_status = True
        self.https = https
        if self.fail_count:
            self.fail_count -= 1
        self.last_time = time.strftime("%Y-%m-%d %H:%M:%S")

    def mark_failure(self) -> None:
        self.check_count += 1
        self.last_status = False
        self.fail_count += 1
        self.last_time = time.strftime("%Y-%m-%d %H:%M:%S")

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Proxy({self.proxy!r}, https={self.https}, fail={self.fail_count}, source={self.source!r})"
