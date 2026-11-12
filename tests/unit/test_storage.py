"""Redis 存储层测试（fakeredis）。"""

from __future__ import annotations

from models import Proxy


class TestCrud:
    def test_put_and_exists(self, store):
        proxy = Proxy("1.2.3.4:8080", sources={"a"})
        store.put(proxy)
        assert store.exists("1.2.3.4:8080")
        assert not store.exists("9.9.9.9:80")

    def test_get_returns_json(self, store, proxy_obj):
        store.put(proxy_obj)
        raw = store.get()
        assert raw is not None
        assert Proxy.from_json(raw).proxy == proxy_obj.proxy

    def test_get_empty(self, store):
        assert store.get() is None
        assert store.get(https=True) is None

    def test_https_filter(self, store):
        store.put(Proxy("1.2.3.4:8080", https=False))
        store.put(Proxy("5.6.7.8:443", https=True))

        assert store.count() == {
            "total": 2,
            "https": 1,
            "http": 1,
            "socks5": 0,
            "socks5_https": 0,
            "grand_total": 2,
            "available": 2,
            "socks5_available": 0,
            "available_total": 2,
        }
        assert len(store.all(https=True)) == 1
        assert len(store.all()) == 2

        # https 取用只会命中 https 索引，多试几次
        seen = {Proxy.from_json(store.get(https=True)).proxy for _ in range(20)}
        assert seen == {"5.6.7.8:443"}

    def test_put_updates_index_when_https_changes(self, store):
        proxy = Proxy("1.2.3.4:8080", https=False)
        store.put(proxy)
        assert store.count()["https"] == 0
        proxy.https = True
        store.put(proxy)
        assert store.count()["https"] == 1
        proxy.https = False
        store.put(proxy)
        assert store.count()["https"] == 0

    def test_delete(self, store):
        store.put(Proxy("1.2.3.4:8080"))
        assert store.delete("1.2.3.4:8080") is True
        assert store.count()["total"] == 0
        assert store.delete("1.2.3.4:8080") is False

    def test_pop_removes(self, store):
        store.put(Proxy("1.2.3.4:8080"))
        raw = store.pop()
        assert raw is not None
        assert store.count()["total"] == 0
        assert store.pop() is None

    def test_put_many(self, store):
        proxies = [Proxy(f"8.8.8.{i}:80", https=i % 2 == 0) for i in range(1, 11)]
        assert store.put_many(proxies) == 10
        assert store.count() == {
            "total": 10,
            "https": 5,
            "http": 5,
            "socks5": 0,
            "socks5_https": 0,
            "grand_total": 10,
            "available": 10,
            "socks5_available": 0,
            "available_total": 10,
        }
        assert store.put_many([]) == 0

    def test_exists_many(self, store):
        store.put(Proxy("1.2.3.4:8080"))
        result = store.exists_many(["1.2.3.4:8080", "9.9.9.9:80"])
        assert result == {"1.2.3.4:8080"}

    def test_clear(self, store):
        store.put(Proxy("1.2.3.4:8080", https=True))
        store.clear()
        assert store.count()["total"] == 0
        assert store.all(https=True) == []


class TestMaintenance:
    def test_source_stats(self, store):
        store.put(Proxy("1.2.3.4:8080", sources={"a", "b"}))
        store.put(Proxy("5.6.7.8:80", sources={"a"}))
        assert store.source_stats() == {"a": 2, "b": 1}

    def test_reindex_rebuilds_sets(self, store, fake_redis):
        fake_redis.hset("test_proxy", "1.2.3.4:8080", Proxy("1.2.3.4:8080", https=True).to_json())
        fake_redis.hset("test_proxy", "5.6.7.8:80", Proxy("5.6.7.8:80").to_json())

        result = store.reindex()
        assert result["total"] == 2
        assert result["https"] == 1
        assert result["socks5"] == 0
        assert store.count() == {
            "total": 2,
            "https": 1,
            "http": 1,
            "socks5": 0,
            "socks5_https": 0,
            "grand_total": 2,
            "available": 2,
            "socks5_available": 0,
            "available_total": 2,
        }

    def test_legacy_data_without_index_is_readable(self, store, fake_redis):
        fake_redis.hset("test_proxy", "1.2.3.4:8080", Proxy("1.2.3.4:8080").to_json())
        # 索引为空时自动重建
        assert store.get() is not None
        assert store.count()["total"] == 1

    def test_legacy_data_is_poppable(self, store, fake_redis):
        """老数据缺索引时 pop 也要能自愈，否则永远返回空。"""
        fake_redis.hset("test_proxy", "1.2.3.4:8080", Proxy("1.2.3.4:8080").to_json())
        assert store.pop() is not None
        assert store.count()["total"] == 0

    def test_count_does_not_reindex_when_no_https_proxy(self, fake_redis, monkeypatch):
        """池内恰好没有 HTTPS 代理时，不应每次 count 都触发索引重建。"""
        from storage.redis_store import RedisStore

        store = RedisStore("redis://localhost:6379/0", "test_proxy", client=fake_redis)
        store.put_many([Proxy(f"8.8.8.{i}:80") for i in range(1, 6)])
        assert fake_redis.scard("test_proxy:all") == 5
        assert fake_redis.scard("test_proxy:https") == 0

        calls: list[str] = []
        original = store.reindex
        monkeypatch.setattr(store, "reindex", lambda scheme=None: calls.append(scheme) or original(scheme))

        for _ in range(3):
            stats = store.count()
            assert stats["https"] == 0
        assert calls == []

    def test_count_reindexes_socks_legacy_data(self, store, fake_redis):
        fake_redis.hset("test_proxy:s5", "1.2.3.4:1080", Proxy("1.2.3.4:1080", scheme="socks5").to_json())
        assert store.count()["socks5"] == 1
        assert fake_redis.scard("test_proxy:s5:all") == 1

    def test_ping(self, store):
        assert store.ping() is True


class TestCheckQueue:
    """增量巡检队列：按「最久未检」排序，跨协议取全局最老的 N 条。"""

    def test_returns_oldest_first(self, store, fake_redis):
        store.put_many([Proxy("8.8.8.1:80"), Proxy("8.8.8.2:80"), Proxy("8.8.8.3:80")])
        fake_redis.zadd("test_proxy:check", {"8.8.8.1:80": 30, "8.8.8.2:80": 10, "8.8.8.3:80": 20})
        batch = store.check_batch(2)
        assert [p.proxy for p in batch] == ["8.8.8.2:80", "8.8.8.3:80"]

    def test_spans_both_schemes(self, store, fake_redis):
        store.put_many([Proxy("8.8.8.1:80"), Proxy("8.8.8.2:80")])
        store.put_many([Proxy("1.1.1.1:1080", scheme="socks5")])
        fake_redis.zadd("test_proxy:check", {"8.8.8.1:80": 100, "8.8.8.2:80": 200})
        fake_redis.zadd("test_proxy:s5:check", {"1.1.1.1:1080": 50})

        batch = store.check_batch(2)
        assert [p.proxy for p in batch] == ["1.1.1.1:1080", "8.8.8.1:80"]
        assert batch[0].scheme == "socks5"

    def test_put_moves_proxy_to_tail(self, store, fake_redis):
        store.put(Proxy("8.8.8.1:80"))
        fake_redis.zadd("test_proxy:check", {"8.8.8.1:80": 0})
        store.put(Proxy("8.8.8.1:80"))  # 重新入库 = 刚校验过
        assert fake_redis.zscore("test_proxy:check", "8.8.8.1:80") > 0

    def test_delete_cleans_queue(self, store, fake_redis):
        store.put(Proxy("8.8.8.1:80"))
        store.delete("8.8.8.1:80")
        assert fake_redis.zcard("test_proxy:check") == 0

    def test_pop_cleans_queue(self, store, fake_redis):
        store.put(Proxy("8.8.8.1:80"))
        store.pop()
        assert fake_redis.zcard("test_proxy:check") == 0

    def test_clear_cleans_both_queues(self, store, fake_redis):
        store.put(Proxy("8.8.8.1:80"))
        store.put(Proxy("1.1.1.1:1080", scheme="socks5"))
        store.clear()
        assert fake_redis.zcard("test_proxy:check") == 0
        assert fake_redis.zcard("test_proxy:s5:check") == 0

    def test_legacy_pool_without_queue_self_heals(self, store, fake_redis):
        fake_redis.hset("test_proxy", "8.8.8.1:80", Proxy("8.8.8.1:80").to_json())
        batch = store.check_batch(10)
        assert [p.proxy for p in batch] == ["8.8.8.1:80"]
        assert fake_redis.zcard("test_proxy:check") == 1

    def test_stale_entries_are_pruned(self, store, fake_redis):
        """hash 已删但队列残留时，取候选应顺手清理而不是返回空对象。"""
        fake_redis.zadd("test_proxy:check", {"9.9.9.9:80": 0})
        assert store.check_batch(10) == []
        assert fake_redis.zcard("test_proxy:check") == 0

    def test_non_positive_limit_is_empty(self, store):
        store.put(Proxy("8.8.8.1:80"))
        assert store.check_batch(0) == []

    def test_reindex_rebuilds_queue(self, store, fake_redis):
        fake_redis.hset("test_proxy", "8.8.8.1:80", Proxy("8.8.8.1:80").to_json())
        store.reindex("http")
        assert fake_redis.zcard("test_proxy:check") == 1


class TestVisibility:
    """校验失败的代理留在池内等复活，但不应再发给客户端。"""

    def test_failed_proxy_is_hidden(self, store):
        store.put(Proxy("8.8.8.1:80", check_count=1, last_status=False, fail_count=1))
        assert store.count()["total"] == 1
        assert store.count()["available"] == 0
        assert store.get() is None
        assert store.pop() is None

    def test_success_brings_it_back(self, store):
        store.put(Proxy("8.8.8.1:80", check_count=1, last_status=False, fail_count=1))
        assert store.get() is None
        store.put(Proxy("8.8.8.1:80", check_count=2, last_status=True))
        assert store.count()["available"] == 1
        assert store.get() is not None

    def test_unchecked_seed_is_visible(self, store):
        """seed 导入的代理从未校验（check_count=0），应当立即可用。"""
        store.put(Proxy("8.8.8.1:80", sources={"manual"}))
        assert store.count()["available"] == 1
        assert store.get() is not None

    def test_all_still_exposes_hidden(self, store):
        """全池复检依赖 all() 能看到下架代理，否则它们永远无法复活。"""
        store.put(Proxy("8.8.8.1:80", check_count=1, last_status=False))
        assert len(store.all()) == 1
        assert store.count()["available"] == 0

    def test_all_hidden_does_not_trigger_reindex(self, store):
        """全部下架时不能误判为索引缺失而把它们重新放出来。"""
        store.put_many([Proxy(f"8.8.8.{i}:80", check_count=1, last_status=False) for i in range(1, 4)])
        assert store.count()["available"] == 0
        assert store.get() is None
        assert store.count()["available"] == 0

    def test_https_index_also_hidden(self, store):
        store.put(Proxy("8.8.8.1:443", https=True, check_count=1, last_status=False))
        assert store.get(https=True) is None
        assert store.count()["https"] == 0


class TestSourceStats:
    """源历史可用率：加权采样的数据来源。"""

    def test_empty(self, store):
        assert store.source_history() == {}

    def test_accumulates_across_rounds(self, store):
        store.record_source_outcomes({"a": 10, "b": 5}, {"a": 3})
        store.record_source_outcomes({"a": 10}, {"a": 4})

        history = store.source_history()
        assert history["a"] == {"checked": 20, "valid": 7}
        assert history["b"] == {"checked": 5, "valid": 0}

    def test_zero_counts_are_ignored(self, store, fake_redis):
        store.record_source_outcomes({}, {"a": 3})
        assert store.source_history() == {}
        assert fake_redis.hlen("test_proxy:src:checked") == 0

    def test_clear_keeps_source_stats(self, store):
        store.put(Proxy("8.8.8.1:80"))
        store.record_source_outcomes({"a": 10}, {"a": 3})
        store.clear()
        assert store.count()["grand_total"] == 0
        assert store.source_history() == {"a": {"checked": 10, "valid": 3}}

    def test_clear_source_stats(self, store):
        store.record_source_outcomes({"a": 10}, {"a": 3})
        store.clear_source_stats()
        assert store.source_history() == {}
