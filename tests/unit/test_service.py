"""业务编排测试。"""

from __future__ import annotations

import asyncio
import dataclasses

from config import get_settings
from fetcher.base import BaseFetcher
from models import Proxy
from service import ProxyPoolService


def run(coro):
    return asyncio.run(coro)


class FakeSource(BaseFetcher):
    name = "fake"
    url = "https://fake.local"
    results: list[str] = ["8.8.8.1:80", "8.8.8.2:80", "10.0.0.1:80"]

    async def fetch(self, session):
        return list(self.results)


class BoomSource(BaseFetcher):
    name = "boom"
    url = "https://boom.local"

    async def fetch(self, session):
        raise RuntimeError("source down")


class SlowSource(FakeSource):
    """模拟响应较慢的源；``timeout`` 与 ``delay`` 由子类覆盖。"""

    name = "slow"
    timeout = 0.01
    delay = 0.15

    async def fetch(self, session):
        await asyncio.sleep(self.delay)
        return ["8.8.8.3:80"]


class PatientSource(SlowSource):
    """自身声明了比全局 SOURCE_TIMEOUT 更长的超时。"""

    name = "patient"
    timeout = 0.5


class FakeValidator:
    def __init__(self, fail: set[str] | None = None):
        self.fail = fail or set()

    def _judge(self, proxy):
        if proxy.proxy in self.fail:
            proxy.mark_failure()
        else:
            proxy.mark_success(True)
        return proxy

    async def validate(self, proxy, session):
        return self._judge(proxy)

    async def validate_many(self, proxies):
        for proxy in proxies:
            self._judge(proxy)
        return list(proxies)


def make_service(store, validator=None, monkeypatch=None, sources=None):
    service = ProxyPoolService(settings=get_settings(), store=store, validator=validator or FakeValidator())
    if monkeypatch is not None:
        monkeypatch.setattr("service.discover_sources", lambda exclude=(): sources or [FakeSource])
    return service


class TestFetch:
    def test_fetch_and_store(self, store, monkeypatch):
        service = make_service(store, monkeypatch=monkeypatch)
        report = run(service.fetch())

        assert report.per_source == {"fake": 2}
        assert report.candidates == 2  # 内网地址被解析层过滤
        assert report.valid == 2
        assert report.stored == 2
        assert store.count()["total"] == 2

        stored = Proxy.from_json(store.get())
        assert stored.source == "fake"

    def test_skip_existing(self, store, monkeypatch):
        store.put(Proxy("8.8.8.1:80", sources={"old"}))
        service = make_service(store, monkeypatch=monkeypatch)
        report = run(service.fetch())
        assert report.candidates == 1
        assert store.count()["total"] == 2

    def test_invalid_proxies_not_stored(self, store, monkeypatch):
        service = make_service(store, FakeValidator(fail={"8.8.8.1:80", "8.8.8.2:80"}), monkeypatch)
        report = run(service.fetch())
        assert report.valid == 0
        assert store.count()["total"] == 0

    def test_source_error_is_captured(self, store, monkeypatch):
        service = make_service(store, monkeypatch=monkeypatch, sources=[FakeSource, BoomSource])
        report = run(service.fetch())
        assert report.per_source == {"fake": 2}
        assert "boom" in report.errors

    def test_source_filter(self, store, monkeypatch):
        service = make_service(store, monkeypatch=monkeypatch)
        report = run(service.fetch(["nonexistent"]))
        assert report.per_source == {}
        assert report.stored == 0

    def test_max_new(self, store, monkeypatch):
        service = make_service(store, monkeypatch=monkeypatch)
        report = run(service.fetch(max_new=1))
        assert report.candidates == 1

    def test_concurrent_fetch_is_skipped(self, store, monkeypatch):
        """巡检触发的抓取与定时抓取撞车时，只应实际执行一次。"""

        class SlowishSource(BaseFetcher):
            name = "slowish"
            url = "https://slowish.local"

            async def fetch(self, session):
                await asyncio.sleep(0.05)
                return ["8.8.8.1:80"]

        monkeypatch.setattr("service.discover_sources", lambda exclude=(): [SlowishSource])
        service = ProxyPoolService(settings=get_settings(), store=store, validator=FakeValidator())

        async def both():
            return await asyncio.gather(service.fetch(), service.fetch())

        reports = run(both())
        assert sum(1 for r in reports if r.per_source) == 1
        assert sum(1 for r in reports if not r.per_source) == 1
        assert store.count()["total"] == 1

    def test_region_lookup_when_enabled(self, store, monkeypatch):
        conf = dataclasses.replace(get_settings(), proxy_region=True, proxy_anonymity=False)
        service = ProxyPoolService(settings=conf, store=store, validator=FakeValidator())
        monkeypatch.setattr("service.discover_sources", lambda exclude=(): [FakeSource])

        async def fake_lookup_many(items, session):
            for _ip, proxy in items:
                proxy.region = "US"
            return len(items)

        monkeypatch.setattr("service.region.lookup_many", fake_lookup_many)
        report = run(service.fetch())
        assert report.stored == 2
        assert report.enriched == {"region": 2, "anonymous": 0}
        assert Proxy.from_json(store.get()).region == "US"

    def test_metadata_disabled_does_nothing(self, store, monkeypatch):
        conf = dataclasses.replace(get_settings(), proxy_region=False, proxy_anonymity=False)
        service = ProxyPoolService(settings=conf, store=store, validator=FakeValidator())
        monkeypatch.setattr("service.discover_sources", lambda exclude=(): [FakeSource])

        async def boom(*args, **kwargs):  # pragma: no cover
            raise AssertionError("关闭后不应发起补全请求")

        monkeypatch.setattr("service.region.lookup_many", boom)
        monkeypatch.setattr("service.anonymity.detect_many", boom)

        report = run(service.fetch())
        assert report.enriched == {"region": 0, "anonymous": 0}

    def test_metadata_enrich_both_fields(self, store, monkeypatch):
        conf = dataclasses.replace(get_settings(), proxy_region=True, proxy_anonymity=True)
        service = ProxyPoolService(settings=conf, store=store, validator=FakeValidator())
        monkeypatch.setattr("service.discover_sources", lambda exclude=(): [FakeSource])

        async def fake_lookup_many(items, session):
            for _ip, proxy in items:
                proxy.region = "中国"
            return len(items)

        async def fake_detect_many(proxies, *, concurrency=32, urls=None):
            for proxy in proxies:
                proxy.anonymous = "high_anonymous"
            return len(proxies)

        monkeypatch.setattr("service.region.lookup_many", fake_lookup_many)
        monkeypatch.setattr("service.anonymity.detect_many", fake_detect_many)

        report = run(service.fetch())
        assert report.enriched == {"region": 2, "anonymous": 2}
        stored = Proxy.from_json(store.get())
        assert stored.region == "中国"
        assert stored.anonymous == "high_anonymous"

    def test_enrich_skips_known_metadata(self, store, monkeypatch):
        """已填过的字段不再重复探测，避免每轮抓取都重跑一遍。"""
        conf = dataclasses.replace(get_settings(), proxy_region=True, proxy_anonymity=True)
        service = ProxyPoolService(settings=conf, store=store, validator=FakeValidator())
        proxy = Proxy("8.8.8.1:80", region="美国", anonymous="anonymous")
        proxy.mark_success(True)

        async def boom(*args, **kwargs):  # pragma: no cover
            raise AssertionError("已有元数据不应再探测")

        monkeypatch.setattr("service.region.lookup_many", boom)
        monkeypatch.setattr("service.anonymity.detect_many", boom)

        assert run(service.enrich([proxy])) == {"region": 0, "anonymous": 0}

    def test_enrich_budget_applies_after_filtering(self, store, monkeypatch):
        """预算截断必须发生在「筛选缺失项」之后。

        顺序反了就会每轮都拿到同一批（已填好值的）代理，剩下的永远轮不到，
        ``python proxyPool.py enrich`` 跑多少遍都补不完。
        """
        conf = dataclasses.replace(get_settings(), proxy_region=True, proxy_anonymity=False, meta_max_per_round=1)
        service = ProxyPoolService(settings=conf, store=store, validator=FakeValidator())

        known = Proxy("1.1.1.1:80", region="美国")
        known.mark_success(True)
        missing = Proxy("2.2.2.2:80")
        missing.mark_success(True)

        async def fake_lookup_many(items, session):
            for _ip, proxy in items:
                proxy.region = "日本"
            return len(items)

        monkeypatch.setattr("service.region.lookup_many", fake_lookup_many)

        # 预算只有 1 条，也必须落在真正缺值的那个代理上
        assert run(service.enrich([known, missing]))["region"] == 1
        assert missing.region == "日本"
        assert known.region == "美国"


class TestSourceTimeout:
    """源级 ``timeout`` 与全局 ``SOURCE_TIMEOUT`` 的取值规则：取较大者。"""

    @staticmethod
    def _service(store, **overrides):
        conf = dataclasses.replace(get_settings(), **overrides)
        return ProxyPoolService(settings=conf, store=store, validator=FakeValidator())

    def test_source_can_extend_global_budget(self, store, monkeypatch):
        monkeypatch.setattr("service.discover_sources", lambda exclude=(): [PatientSource])
        report = run(self._service(store, source_timeout=0.05).fetch())
        assert report.per_source == {"patient": 1}
        assert report.errors == {}

    def test_source_timeout_does_not_shrink_global_budget(self, store, monkeypatch):
        monkeypatch.setattr("service.discover_sources", lambda exclude=(): [SlowSource])
        report = run(self._service(store, source_timeout=0.5).fetch())
        assert report.per_source == {"slow": 1}

    def test_global_budget_still_applies(self, store, monkeypatch):
        monkeypatch.setattr("service.discover_sources", lambda exclude=(): [SlowSource])
        report = run(self._service(store, source_timeout=0.05).fetch())
        assert report.errors == {"slow": "timeout"}
        assert report.per_source == {}


class TestFetchQuota:
    """配额分配：``MAX_NEW_PROXIES`` 与 ``SOCKS_MAX_NEW_PROXIES`` 各自的语义。"""

    @staticmethod
    def _bulk(http_count: int, socks_count: int):
        class HttpBulk(BaseFetcher):
            name = "httpbulk"
            url = "https://h.local"

            async def fetch(self, session):
                return [f"8.8.{i // 250}.{i % 250 + 1}:{1000 + i}" for i in range(http_count)]

        class SocksBulk(BaseFetcher):
            name = "socksbulk"
            url = "https://s.local"
            scheme = "socks5"

            async def fetch(self, session):
                return [f"1.1.{i // 250}.{i % 250 + 1}:{2000 + i}" for i in range(socks_count)]

        return [HttpBulk, SocksBulk]

    class Recording(FakeValidator):
        def __init__(self):
            super().__init__()
            self.schemes: list[str] = []

        async def validate_many(self, proxies):
            self.schemes = [p.scheme for p in proxies]
            return await super().validate_many(proxies)

    def test_socks_quota_survives_http_flood(self, store, monkeypatch):
        """SOCKS5 配额不应被总体 max_new 按比例稀释。

        旧实现把两种协议混在一个列表里按总量截断：4.5 万 HTTP + 5 千 SOCKS5 时，
        800 条的 SOCKS5 配额只剩约 96 条。
        """
        conf = dataclasses.replace(get_settings(), max_new=1000, socks_max_new=300)
        sources = self._bulk(http_count=5000, socks_count=2000)
        monkeypatch.setattr("service.discover_sources", lambda exclude=(): sources)

        validator = self.Recording()
        service = ProxyPoolService(settings=conf, store=store, validator=validator)
        report = run(service.fetch())

        assert report.candidates == 1000
        assert validator.schemes.count("socks5") == 300
        assert validator.schemes.count("http") == 700

    def test_socks_quota_capped_even_when_http_is_small(self, store, monkeypatch):
        conf = dataclasses.replace(get_settings(), max_new=5000, socks_max_new=300)
        sources = self._bulk(http_count=10, socks_count=2000)
        monkeypatch.setattr("service.discover_sources", lambda exclude=(): sources)

        validator = self.Recording()
        service = ProxyPoolService(settings=conf, store=store, validator=validator)
        run(service.fetch())

        assert validator.schemes.count("socks5") == 300
        assert validator.schemes.count("http") == 10

    def test_negative_max_new_does_not_drop_entries(self, store, monkeypatch):
        """负数曾被当作切片负索引，静默丢掉末尾一条。"""
        service = make_service(store, monkeypatch=monkeypatch)
        report = run(service.fetch(max_new=-1))
        assert report.candidates == 2


class TestFetchDirtyData:
    def test_whitespace_and_newline_proxies_are_kept(self, store, monkeypatch):
        """源返回带空格/换行的代理应保留，而不是在 parse 阶段被误判为非法。"""

        class MessySource(BaseFetcher):
            name = "messy"
            url = "https://messy.local"

            async def fetch(self, session):
                return [" 8.8.8.1:80 ", "8.8.8.2:80\n", "\t8.8.8.3:80", "8.8.8.4:0", "8.8.8.5:99999"]

        monkeypatch.setattr("service.discover_sources", lambda exclude=(): [MessySource])
        service = ProxyPoolService(settings=get_settings(), store=store, validator=FakeValidator())
        report = run(service.fetch())

        assert report.per_source == {"messy": 3}
        assert report.stored == 3


class TestCheck:
    def test_check_uses_grand_total_for_refetch(self, store, monkeypatch):
        """只有 SOCKS5 的池不应被判定为空池（count()["total"] 只统计 HTTP）。"""
        conf = dataclasses.replace(get_settings(), pool_size_min=5)
        for i in range(1, 11):
            store.put(Proxy(f"8.8.8.{i}:1080", scheme="socks5"))

        service = ProxyPoolService(settings=conf, store=store, validator=FakeValidator())
        called: list[int] = []

        async def fake_fetch(*args, **kwargs):
            called.append(1)
            return None

        monkeypatch.setattr(service, "fetch", fake_fetch)
        report = run(service.check(refetch_if_low=True))

        assert report.refetched is False
        assert called == []

    def test_removes_dead_proxy(self, store):
        """默认 MAX_FAIL_COUNT=1：连续两次失败才剔除。"""
        store.put(Proxy("8.8.8.1:80"))
        store.put(Proxy("8.8.8.2:80"))
        service = ProxyPoolService(settings=get_settings(), store=store, validator=FakeValidator(fail={"8.8.8.1:80"}))

        first = run(service.check(refetch_if_low=False))
        assert first.removed == 0  # 首次失败只记一次，给瞬时抖动留余地
        assert first.kept == 2

        second = run(service.check(refetch_if_low=False))
        assert second.checked == 2
        assert second.removed == 1
        assert store.count()["total"] == 1
        assert store.exists("8.8.8.2:80")

    def test_single_failure_tolerance(self, store):
        """一次失败后若恢复成功，fail_count 递减，代理得以保留。"""
        store.put(Proxy("8.8.8.1:80", fail_count=1))
        service = ProxyPoolService(settings=get_settings(), store=store, validator=FakeValidator())

        report = run(service.check(refetch_if_low=False))
        assert report.removed == 0
        assert Proxy.from_json(store.get()).fail_count == 0

    def test_keeps_proxy_within_fail_limit(self, store, monkeypatch):
        from dataclasses import replace

        conf = replace(get_settings(), max_fail_count=3)
        store.put(Proxy("8.8.8.1:80"))
        service = ProxyPoolService(settings=conf, store=store, validator=FakeValidator(fail={"8.8.8.1:80"}))

        report = run(service.check(refetch_if_low=False))
        assert report.removed == 0
        assert report.kept == 1
        kept = store.check_batch(10)[0]
        assert kept.fail_count == 1
        # 记录保留在池内等待复活，但已从索引摘下，不再发给客户端
        assert store.count()["available"] == 0
        assert store.get() is None

    def test_empty_pool(self, store):
        service = ProxyPoolService(settings=get_settings(), store=store, validator=FakeValidator())
        report = run(service.check(refetch_if_low=False))
        assert report.checked == 0


class TestIncrementalCheck:
    """巡检按增量队列进行：单轮耗时与池大小解耦。"""

    class Recorder(FakeValidator):
        def __init__(self, fail: set[str] | None = None):
            super().__init__(fail)
            self.seen: list[str] = []

        async def validate_many(self, proxies):
            self.seen.extend(p.proxy for p in proxies)
            return await super().validate_many(proxies)

    @staticmethod
    def _service(store, recorder, **overrides):
        conf = dataclasses.replace(get_settings(), **overrides)
        return ProxyPoolService(settings=conf, store=store, validator=recorder)

    def test_only_limit_proxies_are_checked(self, store):
        store.put_many([Proxy(f"8.8.8.{i}:80") for i in range(1, 6)])
        recorder = self.Recorder()
        service = self._service(store, recorder, check_max_per_round=2)

        report = run(service.check(refetch_if_low=False))
        assert report.checked == 2
        assert report.pool == 5
        assert len(recorder.seen) == 2

    def test_rounds_rotate_through_whole_pool(self, store):
        store.put_many([Proxy(f"8.8.8.{i}:80") for i in range(1, 6)])
        recorder = self.Recorder()
        service = self._service(store, recorder, check_max_per_round=2)

        for _ in range(3):
            run(service.check(refetch_if_low=False))

        # 3 轮 × 2 条 = 6 次，5 个代理各被覆盖一次后开始重复
        assert len(recorder.seen) == 6
        assert set(recorder.seen) == {f"8.8.8.{i}:80" for i in range(1, 6)}
        assert recorder.seen[5] in recorder.seen[:5]

    def test_limit_zero_checks_whole_pool(self, store):
        store.put_many([Proxy(f"8.8.8.{i}:80") for i in range(1, 6)])
        recorder = self.Recorder()
        service = self._service(store, recorder, check_max_per_round=2)

        report = run(service.check(refetch_if_low=False, limit=0))
        assert report.checked == 5

    def test_removed_proxy_leaves_queue(self, store):
        store.put_many([Proxy("8.8.8.1:80"), Proxy("8.8.8.2:80")])
        recorder = self.Recorder(fail={"8.8.8.1:80"})
        service = self._service(store, recorder, check_max_per_round=10)

        run(service.check(refetch_if_low=False))  # 第一次失败只累加计数
        report = run(service.check(refetch_if_low=False))
        assert report.removed == 1
        assert [p.proxy for p in store.check_batch(10)] == ["8.8.8.2:80"]


class TestSourceWeighting:
    """按源历史可用率加权分配校验配额。"""

    class Recording(FakeValidator):
        def __init__(self, fail: set[str] | None = None):
            super().__init__(fail)
            self.sources: list[str] = []

        async def validate_many(self, proxies):
            self.sources = [p.source for p in proxies]
            return await super().validate_many(proxies)

    @staticmethod
    def _two_sources(good_count: int, bad_count: int):
        class GoodSource(BaseFetcher):
            name = "good"
            url = "https://good.local"

            async def fetch(self, session):
                return [f"8.8.{i // 250}.{i % 250 + 1}:{1000 + i}" for i in range(good_count)]

        class BadSource(BaseFetcher):
            name = "bad"
            url = "https://bad.local"

            async def fetch(self, session):
                return [f"1.1.{i // 250}.{i % 250 + 1}:{2000 + i}" for i in range(bad_count)]

        return [GoodSource, BadSource]

    def test_outcomes_are_recorded(self, store, monkeypatch):
        service = make_service(store, FakeValidator(fail={"8.8.8.1:80"}), monkeypatch)
        run(service.fetch())

        history = store.source_history()
        assert history["fake"] == {"checked": 2, "valid": 1}

    def test_outcomes_recorded_even_when_weighting_off(self, store, monkeypatch):
        conf = dataclasses.replace(get_settings(), source_weighting=False)
        service = ProxyPoolService(settings=conf, store=store, validator=FakeValidator())
        monkeypatch.setattr("service.discover_sources", lambda exclude=(): [FakeSource])
        run(service.fetch())
        assert store.source_history()["fake"]["checked"] == 2

    def test_reliable_source_gets_more_quota(self, store, monkeypatch):
        """good 只有 200 条、bad 有 2000 条，但 good 历史可用率是 bad 的十余倍。"""
        store.record_source_outcomes({"good": 100}, {"good": 40})
        store.record_source_outcomes({"bad": 1000}, {"bad": 30})

        conf = dataclasses.replace(get_settings(), max_new=100)
        monkeypatch.setattr("service.discover_sources", lambda exclude=(): self._two_sources(200, 2000))
        recorder = self.Recording()
        service = ProxyPoolService(settings=conf, store=store, validator=recorder)
        run(service.fetch())

        good = recorder.sources.count("good")
        # 均匀抽样下 good 只占 200/2200 ≈ 9 条；加权后应接近一半
        assert good > 30

    def test_uniform_when_no_history(self, store, monkeypatch):
        """没有历史时退化为均匀随机，good 只按条数占比入选。"""
        conf = dataclasses.replace(get_settings(), max_new=100)
        monkeypatch.setattr("service.discover_sources", lambda exclude=(): self._two_sources(200, 2000))
        recorder = self.Recording()
        service = ProxyPoolService(settings=conf, store=store, validator=recorder)
        run(service.fetch())

        good = recorder.sources.count("good")
        assert 1 <= good <= 30

    def test_weighting_can_be_disabled(self, store, monkeypatch):
        store.record_source_outcomes({"good": 100}, {"good": 40})
        store.record_source_outcomes({"bad": 1000}, {"bad": 30})

        conf = dataclasses.replace(get_settings(), max_new=100, source_weighting=False)
        monkeypatch.setattr("service.discover_sources", lambda exclude=(): self._two_sources(200, 2000))
        recorder = self.Recording()
        service = ProxyPoolService(settings=conf, store=store, validator=recorder)
        run(service.fetch())

        assert recorder.sources.count("good") <= 30


class TestMisc:
    def test_stats(self, store):
        store.put(Proxy("8.8.8.1:80", sources={"a"}))
        service = ProxyPoolService(settings=get_settings(), store=store)
        assert service.stats()["total"] == 1
        assert service.stats(detail=True)["source"] == {"a": 1}

    def test_seed(self, store):
        service = ProxyPoolService(settings=get_settings(), store=store)
        assert service.seed(["8.8.8.1:80"], source="file") == 1
        assert Proxy.from_json(store.get()).source == "file"


class TestPickVerified:
    """出仓检测：/get /pop 返回前先实时校验。"""

    def _enable(self, monkeypatch):
        monkeypatch.setenv("GET_VERIFY", "1")
        get_settings.cache_clear()

    def test_failed_candidate_is_skipped(self, store, monkeypatch):
        """失败的候选自动换下一条，最终返回唯一可用者。"""
        self._enable(monkeypatch)
        store.put(Proxy("8.8.8.1:80"))
        store.put(Proxy("8.8.8.2:80"))
        service = make_service(store, FakeValidator(fail={"8.8.8.1:80"}))

        picked = run(service.pick_verified())
        assert picked.proxy == "8.8.8.2:80"

    def test_failed_candidate_removed_from_index(self, store, monkeypatch):
        """失败候选：未超阈值时留在 hash 待复活，但摘出对外索引。"""
        self._enable(monkeypatch)
        store.put(Proxy("8.8.8.1:80"))
        service = make_service(store, FakeValidator(fail={"8.8.8.1:80"}))

        assert run(service.pick_verified()) is None
        counts = store.count()
        assert counts["total"] == 1
        assert counts["available"] == 0

    def test_success_writes_back_status(self, store, monkeypatch):
        """通过出仓检测的代理回写 last_time，并把巡检队列排到队尾。"""
        self._enable(monkeypatch)
        store.put(Proxy("8.8.8.1:80"))
        service = make_service(store)

        picked = run(service.pick_verified())
        assert picked.proxy == "8.8.8.1:80"
        stored = Proxy.from_json(store.get())
        assert stored.last_status is True

    def test_all_fail_returns_none(self, store, monkeypatch):
        self._enable(monkeypatch)
        store.put(Proxy("8.8.8.1:80"))
        service = make_service(store, FakeValidator(fail={"8.8.8.1:80"}))

        assert run(service.pick_verified()) is None
        # 候选只有一条：失败摘出索引后没有更多候选，直接返回 None

    def test_over_fail_count_is_deleted(self, store, monkeypatch):
        """连续失败超过阈值的候选直接删除，不再占用存储。"""
        self._enable(monkeypatch)
        # 已失败 1 次、上次仍存活（在索引中）；本次再失败 fail_count=2 > 阈值 1
        proxy = Proxy("8.8.8.1:80", fail_count=1, last_status=True)
        store.put(proxy)
        service = make_service(store, FakeValidator(fail={"8.8.8.1:80"}))

        assert run(service.pick_verified()) is None
        assert store.count()["total"] == 0

    def test_pop_removes_verified(self, store, monkeypatch):
        self._enable(monkeypatch)
        store.put(Proxy("8.8.8.1:80"))
        service = make_service(store)

        picked = run(service.pick_verified(pop=True))
        assert picked is not None
        assert store.count()["total"] == 0

    def test_pop_failed_is_discarded(self, store, monkeypatch):
        """pop 语义：校验失败的候选已被移除，不回池。"""
        self._enable(monkeypatch)
        store.put(Proxy("8.8.8.1:80"))
        service = make_service(store, FakeValidator(fail={"8.8.8.1:80"}))

        assert run(service.pick_verified(pop=True)) is None
        assert store.count()["total"] == 0

    def test_disabled_keeps_old_behavior(self, store, monkeypatch):
        """开关关闭（或 verify=0）时跳过校验，直接返回索引里的候选。"""
        store.put(Proxy("8.8.8.1:80"))
        # 校验器会把所有代理判失败，但开关关闭时不该被调用
        service = make_service(store, FakeValidator(fail={"8.8.8.1:80"}))

        picked = run(service.pick_verified())
        assert picked.proxy == "8.8.8.1:80"

        picked = run(service.pick_verified(verify=False))
        assert picked.proxy == "8.8.8.1:80"

    def test_empty_store(self, store):
        service = make_service(store)
        assert run(service.pick_verified()) is None
