"""按源历史可用率加权抽样。

各代理源的质量差异极大：实测部分小清单可用率能到 40%，而几万条的公开大清单只有
3%~7%。若按条数均匀抽样，劣质大清单会凭体量把优质源该拿的校验名额全部稀释掉——
单轮 5000 条配额里九成浪费在注定失败的候选上。

这里用每个源的历史可用率作为权重，按 Efraimidis-Spirakis 算法做无放回加权抽样：
为每条候选生成 ``u ** (1/w)`` 作为排序键（``u`` 为均匀随机数），取最大的 k 个。
等权时该算法退化为均匀随机，因此无需单独分支。

权重使用 Beta 先验平滑，避免「样本极少但恰好全成功」的源被高估，也让新源不至于
一开始就被判死：

    rate = (valid + prior * strength) / (checked + strength)

``strength`` 相当于先验的虚拟观测次数，默认 10 次。
"""

from __future__ import annotations

import heapq
import random
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

#: 冷启动先验可用率（同时也是没有任何历史时所有源的权重）
DEFAULT_PRIOR = 0.1
#: 先验的虚拟观测次数，越大越保守
DEFAULT_STRENGTH = 10
#: 权重下限：防止历史表现差的源被完全饿死，保留少量试探
MIN_WEIGHT = 0.02


def smoothed_rate(
    checked: int,
    valid: int,
    *,
    prior: float = DEFAULT_PRIOR,
    strength: int = DEFAULT_STRENGTH,
) -> float:
    """带先验平滑的可用率。"""
    checked = max(int(checked), 0)
    valid = min(max(int(valid), 0), checked)  # 防御脏数据：成功数不可能超过校验数
    strength = max(int(strength), 0)
    if strength == 0:
        return valid / checked if checked else prior
    return (valid + prior * strength) / (checked + strength)


def source_weight(
    names: Iterable[str],
    history: Mapping[str, Mapping[str, int]],
    *,
    prior: float = DEFAULT_PRIOR,
    strength: int = DEFAULT_STRENGTH,
    floor: float = MIN_WEIGHT,
) -> float:
    """一条候选的权重：取其全部来源可用率的均值。

    多源共同收录本身是质量信号，但用 ``max`` 会被单个「看起来很好」的源带偏，
    均值更稳。没有任何历史来源时退回 ``prior``。
    """
    rates: list[float] = []
    for name in names:
        row = history.get(name)
        if not row:
            rates.append(prior)
            continue
        rates.append(
            smoothed_rate(
                int(row.get("checked", 0) or 0),
                int(row.get("valid", 0) or 0),
                prior=prior,
                strength=strength,
            )
        )
    if not rates:
        return max(prior, floor)
    return max(sum(rates) / len(rates), floor)


def weighted_pick(
    items: Sequence[object],
    weights: Sequence[float],
    k: int,
    *,
    rng: Any = None,
) -> list[object]:
    """无放回加权抽样，返回至多 ``k`` 个元素（保持原有相对顺序）。

    ``k >= len(items)`` 时原样返回，避免做无意义的排序。``rng`` 可传入
    ``random.Random(seed)`` 以便测试复现。
    """
    total = len(items)
    if k <= 0:
        return []
    if k >= total:
        return list(items)

    source = rng or random
    keys = [source.random() ** (1.0 / max(w, 1e-9)) for w in weights]
    picked = heapq.nlargest(k, range(total), key=keys.__getitem__)
    picked.sort()
    return [items[i] for i in picked]
