"""加权抽样测试。"""

from __future__ import annotations

import random

from utils import sampling


class TestSmoothedRate:
    def test_cold_start_equals_prior(self):
        assert sampling.smoothed_rate(0, 0, prior=0.1) == 0.1

    def test_converges_to_observed_rate(self):
        # 观测 1000 条、可用 300 条，先验只占很小权重
        rate = sampling.smoothed_rate(1000, 300, prior=0.1, strength=10)
        assert 0.29 < rate < 0.30

    def test_prior_dominates_small_samples(self):
        # 3 条全成功不应直接判成 100%
        assert sampling.smoothed_rate(3, 3, prior=0.1, strength=10) < 0.35

    def test_valid_cannot_exceed_checked(self):
        assert sampling.smoothed_rate(5, 999) <= 1.0
        assert sampling.smoothed_rate(5, 999) == sampling.smoothed_rate(5, 5)

    def test_zero_strength_falls_back_to_raw_ratio(self):
        assert sampling.smoothed_rate(10, 3, strength=0) == 0.3
        assert sampling.smoothed_rate(0, 0, strength=0, prior=0.25) == 0.25

    def test_negative_input_is_clamped(self):
        assert sampling.smoothed_rate(-5, -5, prior=0.1) == 0.1


class TestSourceWeight:
    def test_unknown_source_uses_prior(self):
        assert sampling.source_weight(["nope"], {}) == 0.1

    def test_history_applies(self):
        history = {"good": {"checked": 100, "valid": 40}}
        assert sampling.source_weight(["good"], history) > 0.3

    def test_multiple_sources_average(self):
        history = {
            "good": {"checked": 200, "valid": 100},
            "bad": {"checked": 200, "valid": 4},
        }
        both = sampling.source_weight(["good", "bad"], history)
        solo = sampling.source_weight(["good"], history)
        assert 0.02 < both < solo

    def test_floor_prevents_starvation(self):
        history = {"dead": {"checked": 10000, "valid": 0}}
        assert sampling.source_weight(["dead"], history) == sampling.MIN_WEIGHT

    def test_empty_source_set(self):
        assert sampling.source_weight([], {}) == 0.1


class TestWeightedPick:
    def test_k_zero(self):
        assert sampling.weighted_pick([1, 2, 3], [1, 1, 1], 0) == []

    def test_k_covers_all(self):
        items = ["a", "b", "c"]
        assert sampling.weighted_pick(items, [1, 1, 1], 5) == items

    def test_no_duplicates_and_keeps_order(self):
        items = list(range(100))
        picked = sampling.weighted_pick(items, [0.1] * 100, 10, rng=random.Random(7))
        assert len(picked) == 10
        assert len(set(picked)) == 10
        assert picked == sorted(picked)

    def test_equal_weights_are_uniform(self):
        items = list(range(1000))
        picked = sampling.weighted_pick(items, [0.2] * 1000, 100, rng=random.Random(11))
        # 均匀抽样下 100 个样本覆盖 900 以上的概率极高
        assert len(picked) == 100
        assert len(set(picked)) > 85

    def test_high_weight_wins_most_slots(self):
        """高权重源应占据远多于其条数占比的配额。"""
        good = [("good", i) for i in range(100)]
        bad = [("bad", i) for i in range(900)]
        items = good + bad
        # 权重比 0.5 : 0.05 = 10:1，理论入选比约 50:45
        weights = [0.5] * 100 + [0.05] * 900
        picked = sampling.weighted_pick(items, weights, 100, rng=random.Random(3))
        good_count = sum(1 for tag, _ in picked if tag == "good")
        assert good_count > 40  # 等权时约 10

    def test_zero_weight_does_not_crash(self):
        picked = sampling.weighted_pick(["a", "b", "c"], [0.0, 0.0, 0.0], 2, rng=random.Random(5))
        assert len(picked) == 2
