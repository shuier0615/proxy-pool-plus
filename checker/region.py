"""代理地域补全。

主路径走 ip-api.com 的批量接口（一次最多 100 个 IP），比逐条查询快两个数量级；
批量失败时降级为逐条查询（ip.sb）。返回的国家代码统一转成中文名，例如
``US`` → ``美国``。查不到时**保持空串**，由接口层输出默认值 ``未知`` —— 存储里
留空才能让下一轮继续重试，写死默认值就再也不会去查了。
"""

from __future__ import annotations

import asyncio

import aiohttp

from logging_setup import get_logger
from utils.net import fetch_json

logger = get_logger("region")

#: 地域未知时的展示值。只用于 API 输出，不写入 Redis。
DEFAULT_REGION = "未知"

_BATCH_API = "http://ip-api.com/batch?fields=status,countryCode,query"
#: 逐条兜底接口（批量失败时用）。实测 ip.sb 会 403、ipinfo 会 406，故不再使用。
_SINGLE_APIS: tuple[tuple[str, str], ...] = (
    ("http://ip-api.com/json/{ip}?fields=status,countryCode", "countryCode"),
    ("https://ipapi.co/{ip}/json/", "country"),
)
_BATCH_SIZE = 100
#: ip-api 免费额度是 15 次/分钟，并发 4 批既快又不会撞线
_BATCH_CONCURRENCY = 4
_SINGLE_CONCURRENCY = 16
_CACHE_MAX = 50000

#: ISO 3166-1 alpha-2 → 中文名。未收录的代码原样返回，不会变成空值。
REGION_NAMES: dict[str, str] = {
    "CN": "中国",
    "HK": "中国香港",
    "TW": "中国台湾",
    "MO": "中国澳门",
    # 亚太
    "JP": "日本",
    "KR": "韩国",
    "KP": "朝鲜",
    "MN": "蒙古",
    "SG": "新加坡",
    "MY": "马来西亚",
    "TH": "泰国",
    "VN": "越南",
    "PH": "菲律宾",
    "ID": "印度尼西亚",
    "IN": "印度",
    "PK": "巴基斯坦",
    "BD": "孟加拉国",
    "LK": "斯里兰卡",
    "NP": "尼泊尔",
    "MM": "缅甸",
    "KH": "柬埔寨",
    "LA": "老挝",
    "BN": "文莱",
    "KZ": "哈萨克斯坦",
    "UZ": "乌兹别克斯坦",
    "KG": "吉尔吉斯斯坦",
    "TJ": "塔吉克斯坦",
    "TM": "土库曼斯坦",
    "AF": "阿富汗",
    "AU": "澳大利亚",
    "NZ": "新西兰",
    "FJ": "斐济",
    # 中东 / 西亚
    "IR": "伊朗",
    "IQ": "伊拉克",
    "SA": "沙特阿拉伯",
    "AE": "阿联酋",
    "QA": "卡塔尔",
    "KW": "科威特",
    "BH": "巴林",
    "OM": "阿曼",
    "YE": "也门",
    "JO": "约旦",
    "LB": "黎巴嫩",
    "SY": "叙利亚",
    "IL": "以色列",
    "TR": "土耳其",
    "GE": "格鲁吉亚",
    "AM": "亚美尼亚",
    "AZ": "阿塞拜疆",
    # 欧洲
    "GB": "英国",
    "IE": "爱尔兰",
    "FR": "法国",
    "DE": "德国",
    "IT": "意大利",
    "ES": "西班牙",
    "PT": "葡萄牙",
    "NL": "荷兰",
    "BE": "比利时",
    "LU": "卢森堡",
    "CH": "瑞士",
    "AT": "奥地利",
    "DK": "丹麦",
    "SE": "瑞典",
    "NO": "挪威",
    "FI": "芬兰",
    "IS": "冰岛",
    "PL": "波兰",
    "CZ": "捷克",
    "SK": "斯洛伐克",
    "HU": "匈牙利",
    "RO": "罗马尼亚",
    "BG": "保加利亚",
    "GR": "希腊",
    "HR": "克罗地亚",
    "SI": "斯洛文尼亚",
    "RS": "塞尔维亚",
    "BA": "波黑",
    "ME": "黑山",
    "MK": "北马其顿",
    "AL": "阿尔巴尼亚",
    "LT": "立陶宛",
    "LV": "拉脱维亚",
    "EE": "爱沙尼亚",
    "BY": "白俄罗斯",
    "UA": "乌克兰",
    "MD": "摩尔多瓦",
    "RU": "俄罗斯",
    "CY": "塞浦路斯",
    "MT": "马耳他",
    # 美洲
    "US": "美国",
    "CA": "加拿大",
    "MX": "墨西哥",
    "BR": "巴西",
    "AR": "阿根廷",
    "CL": "智利",
    "CO": "哥伦比亚",
    "PE": "秘鲁",
    "VE": "委内瑞拉",
    "EC": "厄瓜多尔",
    "UY": "乌拉圭",
    "PY": "巴拉圭",
    "BO": "玻利维亚",
    "CR": "哥斯达黎加",
    "PA": "巴拿马",
    "GT": "危地马拉",
    "DO": "多米尼加",
    "CU": "古巴",
    # 非洲
    "ZA": "南非",
    "EG": "埃及",
    "NG": "尼日利亚",
    "KE": "肯尼亚",
    "MA": "摩洛哥",
    "DZ": "阿尔及利亚",
    "TN": "突尼斯",
    "LY": "利比亚",
    "ET": "埃塞俄比亚",
    "GH": "加纳",
    "TZ": "坦桑尼亚",
    "UG": "乌干达",
    "CM": "喀麦隆",
    "CI": "科特迪瓦",
    "SN": "塞内加尔",
    "ZW": "津巴布韦",
    "MU": "毛里求斯",
    "MG": "马达加斯加",
    "MZ": "莫桑比克",
    "AO": "安哥拉",
    "BW": "博茨瓦纳",
    "NA": "纳米比亚",
    "SD": "苏丹",
    "CD": "刚果（金）",
}

#: 进程内缓存：同一 IP 只查一次。多个进程各自持有一份，重复量可忽略。
_cache: dict[str, str] = {}


def region_name(code: str) -> str:
    """国家代码转中文名；未收录的代码原样返回（大写）。"""
    code = (code or "").strip().upper()
    if not code:
        return ""
    return REGION_NAMES.get(code, code)


def _remember(ip: str, code: str) -> None:
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[ip] = code


def clear_cache() -> None:
    _cache.clear()


async def _batch(ips: list[str], session: aiohttp.ClientSession, *, timeout: float) -> dict[str, str]:
    """批量查询，返回 ``{ip: 国家代码}``；失败返回空 dict。"""
    if not ips:
        return {}
    try:
        async with session.post(
            _BATCH_API,
            json=ips,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            if response.status != 200:
                logger.debug("批量地域接口返回 %s", response.status)
                return {}
            rows = await response.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, OSError, EOFError, ValueError) as exc:
        logger.debug("批量地域查询失败：%s", exc)
        return {}

    found: dict[str, str] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict) or row.get("status") != "success":
                continue
            code = str(row.get("countryCode") or "").strip().upper()
            ip = str(row.get("query") or "").strip()
            if code and ip:
                found[ip] = code
    return found


async def lookup(ip: str, session: aiohttp.ClientSession, *, timeout: float = 3.0) -> str:
    """查询单个 IP 的国家代码，失败返回空串。"""
    cached = _cache.get(ip)
    if cached:
        return cached
    for template, field in _SINGLE_APIS:
        payload = await fetch_json(session, template.format(ip=ip), timeout=timeout, retries=0)
        if not isinstance(payload, dict):
            continue
        code = str(payload.get(field) or "").strip().upper()
        # 2 位才认，避免把接口的错误文案当成国家代码
        if len(code) == 2 and code.isalpha():
            _remember(ip, code)
            return code
    return ""


async def lookup_many(items: list[tuple[str, object]], session: aiohttp.ClientSession) -> int:
    """批量填充 ``Proxy.region``（中文名），返回实际填充的条数。

    仅对尚无地域信息的代理发起查询；同一 IP 的多个端口合并成一次查询。
    """
    grouped: dict[str, list[object]] = {}
    for ip, proxy in items:
        if ip and not getattr(proxy, "region", ""):
            grouped.setdefault(ip, []).append(proxy)
    if not grouped:
        return 0

    resolved: dict[str, str] = {}
    unknown: list[str] = []
    for ip in grouped:
        cached = _cache.get(ip)
        if cached:
            resolved[ip] = cached
        else:
            unknown.append(ip)

    if unknown:
        chunks = [unknown[i : i + _BATCH_SIZE] for i in range(0, len(unknown), _BATCH_SIZE)]
        semaphore = asyncio.Semaphore(_BATCH_CONCURRENCY)

        async def run(chunk: list[str]) -> None:
            async with semaphore:
                found = await _batch(chunk, session, timeout=8.0)
            for ip, code in found.items():
                if ip not in resolved:
                    resolved[ip] = code
                    _remember(ip, code)

        async with asyncio.TaskGroup() as group:
            for chunk in chunks:
                group.create_task(run(chunk))

    # 批量接口漏掉的（限流 / IP 非法 / 接口不可达）逐条兜底
    missing = [ip for ip in unknown if ip not in resolved]
    if missing:
        semaphore = asyncio.Semaphore(_SINGLE_CONCURRENCY)

        async def single(ip: str) -> None:
            async with semaphore:
                code = await lookup(ip, session)
            if code:
                resolved[ip] = code

        async with asyncio.TaskGroup() as group:
            for ip in missing:
                group.create_task(single(ip))

    filled = 0
    for ip, targets in grouped.items():
        code = resolved.get(ip)
        if not code:
            continue
        name = region_name(code)
        for target in targets:
            target.region = name  # type: ignore[attr-defined]
            filled += 1
    if filled:
        logger.debug("地域补全：%d 个 IP，命中 %d 条代理", len(grouped), filled)
    return filled
