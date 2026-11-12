"""Redis 存储实现。

数据结构（均为同一 Redis 库，按代理协议隔离命名空间）：

===================  ==========  ======================================
key                  类型        说明
===================  ==========  ======================================
``{table}``          hash        HTTP 代理：``proxy -> Proxy JSON``
``{table}:all``      set         全部 HTTP 代理（用于 O(1) 随机取用）
``{table}:https``    set         支持 HTTPS 的 HTTP 代理
``{table}:check``    zset        HTTP 巡检队列：``proxy -> 上次校验时间戳``
``{table}:s5``       hash        SOCKS5 代理：``proxy -> Proxy JSON``
``{table}:s5:all``   set         全部 SOCKS5 代理
``{table}:s5:https`` set         支持 HTTPS 的 SOCKS5 代理
``{table}:s5:check`` zset        SOCKS5 巡检队列
``{table}:src:checked`` hash     源历史：``source -> 已校验条数``
``{table}:src:valid``   hash     源历史：``source -> 其中可用条数``
===================  ==========  ======================================

两种协议独立存放，是因为 ``1.2.3.4:1080`` 既可能是 HTTP 代理也可能是 SOCKS5
代理，共用一个 hash 会互相覆盖。HTTP 相关 key 与 2.x/3.0 完全一致，老数据无需迁移；
SOCKS5 使用独立前缀，未启用时不会产生任何额外读写。

随机取用与计数均为 O(1)：10 万级代理下 ``/get`` 的 P99 延迟在 1ms 以内。

巡检队列（``*:check``）让巡检开销与池大小解耦：每轮只取 zset 中分数最小（最久
未检）的 N 条，而不是把全池读出来复检。分数就是该代理上次校验的时间戳。
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from redis import Redis
from redis.connection import BlockingConnectionPool

from logging_setup import get_logger
from models import Proxy

logger = get_logger("storage")

#: 支持的代理协议；与 ``Proxy.scheme`` 取值一致
SCHEMES = ("http", "socks5")


class RedisStore:
    """代理池持久层。"""

    def __init__(
        self,
        db_conn: str,
        table: str = "use_proxy",
        *,
        max_connections: int = 32,
        client: Redis | None = None,
    ) -> None:
        self.key = table
        self.set_all = f"{table}:all"
        self.set_https = f"{table}:https"
        self.socks_key = f"{table}:s5"
        self.socks_all = f"{table}:s5:all"
        self.socks_https = f"{table}:s5:https"
        self.check_zset = f"{table}:check"
        self.socks_check_zset = f"{table}:s5:check"
        self.source_checked_key = f"{table}:src:checked"
        self.source_valid_key = f"{table}:src:valid"
        self._client = client or Redis(
            connection_pool=BlockingConnectionPool.from_url(
                db_conn,
                decode_responses=True,
                max_connections=max_connections,
                protocol=2,  # RESP2：兼容 Redis 5/6 及 KeyDB 等衍生实现
                socket_timeout=5,
                socket_connect_timeout=5,
                health_check_interval=30,
            )
        )

    # ------------------------------------------------------------------ 内部
    def _keys(self, scheme: str) -> tuple[str, str, str]:
        """返回 ``(hash, all_set, https_set)``。"""
        if scheme == "socks5":
            return self.socks_key, self.socks_all, self.socks_https
        return self.key, self.set_all, self.set_https

    def _zkey(self, scheme: str) -> str:
        """返回巡检队列（zset）键名。"""
        return self.socks_check_zset if scheme == "socks5" else self.check_zset

    def _index_keys(self) -> tuple[str, ...]:
        return (self.set_all, self.set_https, self.socks_all, self.socks_https)

    # ------------------------------------------------------------------ 读取
    def get(self, https: bool = False, scheme: str = "http") -> str | None:
        """随机返回一个代理的 JSON 字符串。"""
        hash_key, all_set, https_set = self._keys(scheme)
        key = https_set if https else all_set
        for _ in range(3):  # 索引集合可能与 hash 短暂不一致，重试几次
            proxy = self._client.srandmember(key)
            if proxy is None:
                self._ensure_index()
                proxy = self._client.srandmember(key)
            if proxy is None:
                return None
            payload = self._client.hget(hash_key, proxy)
            if payload:
                return payload
            self._client.srem(all_set, proxy)
            self._client.srem(https_set, proxy)
        return None

    def pop(self, https: bool = False, scheme: str = "http") -> str | None:
        """随机取出并删除一个代理。"""
        hash_key, all_set, https_set = self._keys(scheme)
        key = https_set if https else all_set
        for _ in range(3):
            proxy = self._client.spop(key)
            if proxy is None:
                if not https:
                    # 与 get() 保持一致：老数据缺索引时自愈，否则 pop 永远取不到
                    self._ensure_index()
                    proxy = self._client.spop(key)
                if proxy is None:
                    return None
            payload = self._client.hget(hash_key, proxy)
            self._client.srem(all_set, proxy)
            self._client.srem(https_set, proxy)
            if payload:
                self._client.hdel(hash_key, proxy)
                self._client.zrem(self._zkey(scheme), proxy)
                return payload
        return None

    def all(self, https: bool = False, scheme: str = "http") -> list[str]:
        """返回全部代理 JSON；``https=True`` 时仅返回支持 HTTPS 的。"""
        if scheme == "all":
            return [raw for name in SCHEMES for raw in self.all(https, name)]

        hash_key, _, https_set = self._keys(scheme)
        if not https:
            return list(self._client.hvals(hash_key))
        members = self._client.smembers(https_set)
        if not members:
            self._ensure_index()
            members = self._client.smembers(https_set)
        if not members:
            return []
        return [raw for raw in self._client.hmget(hash_key, list(members)) if raw]

    def exists(self, proxy: str, scheme: str = "http") -> bool:
        return bool(self._client.hexists(self._keys(scheme)[0], proxy))

    def exists_many(self, proxies: Iterable[str], scheme: str = "http") -> set[str]:
        """批量判断存在性，避免 N 次往返。"""
        items = list(proxies)
        if not items:
            return set()
        hash_key = self._keys(scheme)[0]
        pipe = self._client.pipeline(transaction=False)
        for proxy in items:
            pipe.hexists(hash_key, proxy)
        return {proxy for proxy, hit in zip(items, pipe.execute(), strict=True) if hit}

    def count(self) -> dict[str, int]:
        # 8 次读合并为一次往返：/count 是最高频的接口，逐条 SCARD 会累积成可观延迟
        pipe = self._client.pipeline(transaction=False)
        pipe.hlen(self.key)
        pipe.hlen(self.socks_key)
        pipe.scard(self.set_all)
        pipe.scard(self.set_https)
        pipe.scard(self.socks_all)
        pipe.scard(self.socks_https)
        pipe.zcard(self.check_zset)
        pipe.zcard(self.socks_check_zset)
        (
            http_total,
            socks_total,
            http_all,
            http_https,
            socks_all,
            socks_https,
            http_queue,
            socks_queue,
        ) = pipe.execute()

        # 老数据（只有 hash、没有索引集合也没有巡检队列）自动重建。
        # 判据必须带上巡检队列：正常运行时 put_many 一定会写队列，队列为空才说明
        # 索引是真的丢了；否则「代理恰好全部处于下架状态」会被误判成索引缺失，
        # 把它们重新放出到 /get。
        if http_total and not http_all and not http_queue:
            self.reindex("http")
            http_all = self._client.scard(self.set_all)
            http_https = self._client.scard(self.set_https)
        if socks_total and not socks_all and not socks_queue:
            self.reindex("socks5")
            socks_all = self._client.scard(self.socks_all)
            socks_https = self._client.scard(self.socks_https)

        return {
            "total": http_total,  # 向后兼容：旧版字段只统计 HTTP 代理
            "https": http_https,
            "http": max(http_total - http_https, 0),
            "socks5": socks_total,
            "socks5_https": socks_https,
            "grand_total": http_total + socks_total,
            # 当前真正对外提供的数量：校验失败一次、等待复活的代理不算在内
            "available": http_all,
            "socks5_available": socks_all,
            "available_total": http_all + socks_all,
        }

    def source_stats(self) -> dict[str, int]:
        """按来源统计（需全量扫描，仅在 /count?detail=1 时调用）。"""
        stats: dict[str, int] = {}
        for key in (self.key, self.socks_key):
            for raw in self._client.hvals(key):
                for source in (Proxy.from_json(raw).source or "unknown").split("/"):
                    stats[source] = stats.get(source, 0) + 1
        return dict(sorted(stats.items(), key=lambda kv: kv[1], reverse=True))

    def source_history(self) -> dict[str, dict[str, int]]:
        """各源累计的 ``{"checked": n, "valid": m}``，用于加权采样。

        只统计**新代理校验**的结果（每轮抓取后累加），不含池内巡检，因此
        反映的是「从该源捞到的代理有多大比例第一次就能用」。
        """
        checked = self._client.hgetall(self.source_checked_key)
        valid = self._client.hgetall(self.source_valid_key)
        return {
            name: {
                "checked": int(checked.get(name, 0) or 0),
                "valid": int(valid.get(name, 0) or 0),
            }
            for name in set(checked) | set(valid)
        }

    # ------------------------------------------------------------------ 写入
    def put(self, proxy: Proxy) -> None:
        self.put_many([proxy])

    def put_many(self, proxies: list[Proxy]) -> int:
        if not proxies:
            return 0
        now = time.time()
        pipe = self._client.pipeline(transaction=False)
        for proxy in proxies:
            hash_key, all_set, https_set = self._keys(proxy.scheme)
            pipe.hset(hash_key, proxy.proxy, proxy.to_json())
            # 写入即「刚校验过」，在巡检队列里排到队尾，避免刚入库的代理被重复复检
            pipe.zadd(self._zkey(proxy.scheme), {proxy.proxy: now})
            # 只有「校验通过」或「从未校验」（seed 手工导入，check_count==0）
            # 的代理对外提供。校验失败但未达剔除阈值的会留在 hash 里等下次
            # 巡检复活，但必须先摘出索引——否则 MAX_FAIL_COUNT 的容错会让
            # /get 把刚失败的代理继续发给客户端。
            if proxy.last_status or proxy.check_count == 0:
                pipe.sadd(all_set, proxy.proxy)
                if proxy.https:
                    pipe.sadd(https_set, proxy.proxy)
                else:
                    pipe.srem(https_set, proxy.proxy)
            else:
                pipe.srem(all_set, proxy.proxy)
                pipe.srem(https_set, proxy.proxy)
        pipe.execute()
        return len(proxies)

    def delete(self, proxy: str, scheme: str = "http") -> bool:
        hash_key, all_set, https_set = self._keys(scheme)
        pipe = self._client.pipeline(transaction=False)
        pipe.hdel(hash_key, proxy)
        pipe.srem(all_set, proxy)
        pipe.srem(https_set, proxy)
        pipe.zrem(self._zkey(scheme), proxy)
        removed, _, _, _ = pipe.execute()
        return bool(removed)

    def delete_any(self, proxy: str) -> list[str]:
        """两种命名空间都尝试删除，返回实际命中的协议。"""
        hit = [scheme for scheme in SCHEMES if self.delete(proxy, scheme)]
        return hit

    def record_source_outcomes(self, checked: dict[str, int], valid: dict[str, int]) -> None:
        """累加各源本轮的校验结果（供加权采样使用）。"""
        if not checked:
            return
        pipe = self._client.pipeline(transaction=False)
        for name, count in checked.items():
            if count > 0:
                pipe.hincrby(self.source_checked_key, name, count)
        for name, count in valid.items():
            if count > 0:
                pipe.hincrby(self.source_valid_key, name, count)
        pipe.execute()

    def clear_source_stats(self) -> None:
        self._client.delete(self.source_checked_key, self.source_valid_key)

    def clear(self) -> None:
        # 源可用率统计属于跨轮次的学习数据，不随代理池清空而丢失；
        # 需要一并重置时调用 clear_source_stats()（CLI: clear --stats）。
        self._client.delete(
            self.key,
            self.socks_key,
            *self._index_keys(),
            self.check_zset,
            self.socks_check_zset,
        )

    # ------------------------------------------------------------------ 巡检队列
    def check_batch(self, limit: int) -> list[Proxy]:
        """按「最久未校验」顺序取出至多 ``limit`` 条代理。

        跨协议取全局最老的 N 条：先从各自 zset 各取 ``limit`` 条，再归并截断。
        这样做是精确的——若某成员属于全局最老的 N 条，它必然也属于自己的 zset
        中最老的 N 条。
        """
        if limit <= 0:
            return []
        self._ensure_check_index()

        picked: list[tuple[float, str, str]] = []
        for scheme in SCHEMES:
            rows = self._client.zrange(self._zkey(scheme), 0, limit - 1, withscores=True)
            picked.extend((float(score), scheme, name) for name, score in rows)
        if not picked:
            return []
        picked.sort(key=lambda item: item[0])
        picked = picked[:limit]

        grouped: dict[str, list[str]] = {}
        for _score, scheme, name in picked:
            grouped.setdefault(scheme, []).append(name)

        result: list[Proxy] = []
        stale: list[tuple[str, str]] = []
        for scheme, names in grouped.items():
            hash_key = self._keys(scheme)[0]
            for name, raw in zip(names, self._client.hmget(hash_key, names), strict=True):
                if raw:
                    result.append(Proxy.from_json(raw))
                else:
                    # hash 已删但 zset 残留（旧数据或异常中断），顺手清理
                    stale.append((self._zkey(scheme), name))
        if stale:
            pipe = self._client.pipeline(transaction=False)
            for zkey, name in stale:
                pipe.zrem(zkey, name)
            pipe.execute()
        return result

    def _ensure_check_index(self) -> None:
        """老数据（有 hash 无巡检队列）自动补建，分数置 0 优先排队。"""
        for scheme in SCHEMES:
            hash_key = self._keys(scheme)[0]
            if self._client.hlen(hash_key) and not self._client.zcard(self._zkey(scheme)):
                self._rebuild_check_queue(scheme)

    def _rebuild_check_queue(self, scheme: str) -> int:
        """从 hash 重建巡检队列；``nx=True`` 保留已有时间戳不覆盖。"""
        hash_key = self._keys(scheme)[0]
        zkey = self._zkey(scheme)
        total = 0
        batch: list[str] = []
        for name, _raw in self._client.hscan_iter(hash_key, count=1000):
            batch.append(name)
            if len(batch) >= 1000:
                self._client.zadd(zkey, dict.fromkeys(batch, 0.0), nx=True)
                total += len(batch)
                batch = []
        if batch:
            self._client.zadd(zkey, dict.fromkeys(batch, 0.0), nx=True)
            total += len(batch)
        logger.info("重建巡检队列（%s）：%d 条", scheme, total)
        return total

    # ------------------------------------------------------------------ 维护
    def _ensure_index(self) -> None:
        """老数据（仅 hash）缺少索引集合时自动重建。

        同样要求巡检队列也为空，避免「全部代理都被下架」时误重建。
        """
        for scheme in SCHEMES:
            hash_key, all_set, _ = self._keys(scheme)
            if (
                self._client.hlen(hash_key)
                and not self._client.scard(all_set)
                and not self._client.zcard(self._zkey(scheme))
            ):
                self.reindex(scheme)

    def reindex(self, scheme: str | None = None) -> dict[str, int]:
        """依据 hash 内容重建索引；``scheme=None`` 时重建全部协议。"""
        if scheme is None:
            http = self.reindex("http")
            socks = self.reindex("socks5")
            return {
                "total": http["total"] + socks["total"],
                "http": http["total"],
                "http_https": http["https"],
                "socks5": socks["total"],
                "socks5_https": socks["https"],
                "https": http["https"] + socks["https"],
            }

        hash_key, all_set, https_set = self._keys(scheme)
        self._client.delete(all_set, https_set)

        total = https = 0
        plain_batch: list[str] = []
        https_batch: list[str] = []
        for proxy_str, raw in self._client.hscan_iter(hash_key, count=1000):
            total += 1
            plain_batch.append(proxy_str)
            if Proxy.from_json(raw).https:
                https += 1
                https_batch.append(proxy_str)
            if len(plain_batch) >= 1000:
                self._flush_index(all_set, https_set, plain_batch, https_batch)
                plain_batch, https_batch = [], []
        if plain_batch:
            self._flush_index(all_set, https_set, plain_batch, https_batch)

        # 巡检队列一并补建，否则老数据 / 手工 reindex 后会永远取不到候选
        self._rebuild_check_queue(scheme)

        logger.info("重建索引完成（%s）：total=%d https=%d", scheme, total, https)
        return {"total": total, "https": https}

    def _flush_index(self, all_set: str, https_set: str, plain: list[str], https: list[str]) -> None:
        pipe = self._client.pipeline(transaction=False)
        if plain:
            pipe.sadd(all_set, *plain)
        if https:
            pipe.sadd(https_set, *https)
        pipe.execute()

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception as exc:  # redis 连接类异常
            logger.error("Redis 连接失败：%s", exc)
            return False
