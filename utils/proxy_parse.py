"""代理字符串解析与合法性校验。

所有代理源最终都收敛到 ``host:port``（可选 ``user:pass@host:port``）格式，
由本模块统一处理，避免每个源各写一套正则。
"""

from __future__ import annotations

import ipaddress
import re

# 支持三种常见写法：
#   1.2.3.4:8080
#   1.2.3.4 8080
#   user:pass@1.2.3.4:8080
_PROXY_RE = re.compile(
    r"(?<![\d.])"
    r"(?:(?P<auth>[^\s:@/]{1,64}:[^\s:@/]{1,64})@)?"
    r"(?P<ip>(?:\d{1,3}\.){3}\d{1,3})"
    r"(?::|\s+|\s*,\s*)"
    r"(?P<port>\d{1,5})"
    r"(?!\d)"
)

_PORT_MIN = 1
_PORT_MAX = 65535

# 明显不可能作为公网出口代理的网段，提前过滤以节省校验开销
_SKIP_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)


def is_public_ip(ip: str) -> bool:
    """判断是否公网单播地址。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_private or addr.is_loopback or addr.is_multicast or addr.is_reserved:
        return False
    return not any(addr in net for net in _SKIP_NETWORKS)


def normalize(ip: str, port: str | int, auth: str | None = None) -> str | None:
    """校验并拼接代理字符串，非法返回 ``None``。"""
    if not ip or not is_public_ip(ip):
        return None
    try:
        port_num = int(port)
    except (TypeError, ValueError):
        return None
    if not _PORT_MIN <= port_num <= _PORT_MAX:
        return None
    return f"{auth}@{ip}:{port_num}" if auth else f"{ip}:{port_num}"


def parse_proxies(text: str | None, *, limit: int | None = None) -> list[str]:
    """从任意文本中提取代理，按出现顺序去重。

    >>> parse_proxies("1.2.3.4:8080\\n5.6.7.8 3128")
    ['1.2.3.4:8080', '5.6.7.8:3128']
    """
    if not text:
        return []

    seen: set[str] = set()
    result: list[str] = []
    for match in _PROXY_RE.finditer(text):
        proxy = normalize(match["ip"], match["port"], match["auth"])
        if proxy is None or proxy in seen:
            continue
        seen.add(proxy)
        result.append(proxy)
        if limit is not None and len(result) >= limit:
            break
    return result


def parse_proxy(proxy: str) -> tuple[str, int] | None:
    """拆分 ``[user:pass@]host:port``，非法返回 ``None``。

    端口必须落在 1~65535：``1.2.3.4:0`` / ``1.2.3.4:99999`` 这类脏数据此前能通过，
    白占一个校验配额。首尾空白也在这里统一剔除，调用方不必先 strip。
    """
    host = proxy.strip().rsplit("@", 1)[-1]
    ip, _, port = host.rpartition(":")
    if not ip or not port.isdigit():
        return None
    port_num = int(port)
    if not _PORT_MIN <= port_num <= _PORT_MAX:
        return None
    if not is_public_ip(ip):
        return None
    return ip, port_num
