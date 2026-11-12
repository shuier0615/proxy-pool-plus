"""代理池业务编排：抓取 → 校验 → 入库 / 巡检。"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from checker import anonymity, region
from checker.validator import Validator, socks_supported
from config import Settings, get_settings
from fetcher.registry import discover_sources
from logging_setup import get_logger
from models import Proxy
from storage.redis_store import SCHEMES, RedisStore
from utils import sampling
from utils.net import build_session
from utils.proxy_parse import parse_proxy

logger = get_logger("service")


@dataclass(slots=True)
class FetchReport:
    per_source: dict[str, int] = field(default_factory=dict)
    raw_total: int = 0
    candidates: int = 0
    valid: int = 0
    stored: int = 0
    elapsed: float = 0.0
    errors: dict[str, str] = field(default_factory=dict)
    #: 本轮补全的元数据条数：``{"region": n, "anonymous": m}``
    enriched: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        extra = ""
        if self.enriched:
            extra = f"（地域 {self.enriched.get('region', 0)} / 匿名度 {self.enriched.get('anonymous', 0)}）"
        return (
            f"抓取完成：{len(self.per_source)} 个源 / 原始 {self.raw_total} / "
            f"待校验 {self.candidates} / 可用 {self.valid} / 入库 {self.stored}{extra} / "
            f"耗时 {self.elapsed:.1f}s"
        )


@dataclass(slots=True)
class CheckReport:
    checked: int = 0
    valid: int = 0
    removed: int = 0
    kept: int = 0
    pool: int = 0
    elapsed: float = 0.0
    refetched: bool = False

    def summary(self) -> str:
        return (
            f"巡检完成：本轮检测 {self.checked} / 池 {self.pool} / 可用 {self.valid} / "
            f"剔除 {self.removed} / 保留 {self.kept} / 耗时 {self.elapsed:.1f}s"
        )


class ProxyPoolService:
    """对外统一入口，CLI / 调度器 / API 共用。"""

    def __init__(
        self,
        settings: Settings | None = None,
        store: RedisStore | None = None,
        validator: Validator | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or RedisStore(self.settings.db_conn, self.settings.table_name)
        self.validator = validator or Validator(self.settings)
        # 抓取可能持续数分钟，而巡检每 2 分钟一次；池子不足时巡检会主动触发抓取，
        # 与定时抓取撞车就会重复下载整批清单、双倍消耗校验配额。
        self._fetch_lock = asyncio.Lock()

    # ------------------------------------------------------------------ 抓取
    async def fetch(
        self,
        source_names: Sequence[str] | None = None,
        *,
        max_per_source: int | None = None,
        max_new: int | None = None,
        skip_existing: bool = True,
    ) -> FetchReport:
        """抓取入口；同一实例内串行，撞上执行中的抓取时直接返回空报告。"""
        if self._fetch_lock.locked():
            logger.info("上一轮抓取尚未结束，跳过本次触发")
            return FetchReport()
        async with self._fetch_lock:
            return await self._fetch(
                source_names,
                max_per_source=max_per_source,
                max_new=max_new,
                skip_existing=skip_existing,
            )

    async def _fetch(
        self,
        source_names: Sequence[str] | None = None,
        *,
        max_per_source: int | None = None,
        max_new: int | None = None,
        skip_existing: bool = True,
    ) -> FetchReport:
        started = time.perf_counter()
        report = FetchReport()

        max_per_source = max_per_source or self.settings.max_per_source
        max_new = max_new or self.settings.max_new

        sources = discover_sources(self.settings.fetcher_exclude)
        if source_names:
            wanted = {name.lower() for name in source_names}
            sources = [cls for cls in sources if cls.name.lower() in wanted or cls.__name__.lower() in wanted]
        if self.settings.socks_max_new <= 0 or not socks_supported():
            skipped = [cls.name for cls in sources if cls.scheme == "socks5"]
            if skipped:
                reason = "SOCKS_MAX_NEW_PROXIES=0" if self.settings.socks_max_new <= 0 else "未安装 aiohttp-socks"
                logger.warning("跳过 SOCKS5 源（%s）：%s", reason, ", ".join(skipped))
            sources = [cls for cls in sources if cls.scheme != "socks5"]
        if not sources:
            logger.warning("没有可用的代理源")
            return report

        logger.info("开始抓取，启用源 %d 个：%s", len(sources), ", ".join(c.name for c in sources))
        collected: dict[tuple[str, str], set[str]] = {}
        semaphore = asyncio.Semaphore(max(1, self.settings.fetch_concurrency))

        async with build_session(limit=max(8, self.settings.fetch_concurrency)) as session:

            async def run(cls) -> None:
                async with semaphore:
                    # 源可声明更长的 timeout（如 proxifly 的整包 JSON），
                    # 否则 SOURCE_TIMEOUT 会把源自身的超时设置直接吃掉。
                    budget = max(self.settings.source_timeout, float(cls.timeout))
                    try:
                        async with asyncio.timeout(budget):
                            proxies = await cls().fetch(session)
                    except TimeoutError:
                        report.errors[cls.name] = "timeout"
                        logger.warning("源超时：%s", cls.name)
                        return
                    except Exception as exc:  # 单源失败不影响整体
                        report.errors[cls.name] = str(exc)[:120]
                        logger.warning("源抓取失败 %s: %s", cls.name, exc)
                        return

                    if max_per_source and len(proxies) > max_per_source:
                        proxies = random.sample(proxies, max_per_source)
                    # 个别源会返回空行/内网地址/带协议前缀的脏数据，这里统一过滤。
                    # 必须先 strip 再校验：否则 " 1.2.3.4:80 " 会在 parse_proxy 里被判非法而丢弃
                    proxies = [p.strip() for p in proxies if p]
                    proxies = [p for p in proxies if parse_proxy(p)]
                    report.per_source[cls.name] = len(proxies)
                    for proxy in proxies:
                        collected.setdefault((cls.scheme, proxy), set()).add(cls.name)
                    logger.info("源 %-14s 获取 %d 个", cls.name, len(proxies))

            async with asyncio.TaskGroup() as group:
                for cls in sources:
                    group.create_task(run(cls))

        report.raw_total = sum(report.per_source.values())
        candidates = list(collected.items())
        random.shuffle(candidates)

        if skip_existing:
            existing: set[tuple[str, str]] = set()
            for scheme in SCHEMES:
                batch = [key[1] for key, _src in candidates if key[0] == scheme]
                existing |= {(scheme, proxy) for proxy in self.store.exists_many(batch, scheme)}
            if existing:
                candidates = [item for item in candidates if item[0] not in existing]
                logger.info("跳过已存在代理 %d 个", len(existing))

        # 配额按协议分别分配后再合并：二者混在一个列表里按总量截断，会把 SOCKS5
        # 按其在候选中的占比稀释掉（实测 4.5 万 HTTP + 5 千 SOCKS5 时，800 的
        # SOCKS5 配额只剩 96 条）。
        #
        # 再按源历史可用率加权：劣质大清单只有 3%~7% 可用，却常占候选的九成，
        # 均匀抽样会把优质小源(40%+)的名额全稀释掉。权重见 utils/sampling.py。
        history = self.store.source_history() if self.settings.source_weighting else {}
        prior = self.settings.source_weight_prior
        strength = self.settings.source_weight_strength

        def weight_of(item: tuple[tuple[str, str], set[str]]) -> float:
            return sampling.source_weight(item[1], history, prior=prior, strength=strength)

        http_items = [item for item in candidates if item[0][0] != "socks5"]
        socks_items = [item for item in candidates if item[0][0] == "socks5"]
        if len(socks_items) > self.settings.socks_max_new:
            socks_items = sampling.weighted_pick(
                socks_items,
                [weight_of(item) for item in socks_items],
                self.settings.socks_max_new,
            )

        # max_new 是单轮校验总量上限；<=0 视为不限制（避免负数被当成 Python 切片
        # 的负索引，静默丢掉列表末尾元素）
        if max_new and max_new > 0:
            http_quota = max(max_new - len(socks_items), 0)
            if len(http_items) > http_quota:
                http_items = sampling.weighted_pick(
                    http_items,
                    [weight_of(item) for item in http_items],
                    http_quota,
                )
        candidates = http_items + socks_items

        report.candidates = len(candidates)
        if not candidates:
            report.elapsed = time.perf_counter() - started
            logger.info(report.summary())
            return report

        proxies = [Proxy(proxy, scheme=scheme, sources=src) for (scheme, proxy), src in candidates]
        checked = await self.validator.validate_many(proxies)
        valid = [p for p in checked if p.last_status]
        report.valid = len(valid)

        # 累加各源本轮的战绩，供下一轮加权采样。即便关闭了加权也照常记录，
        # 这样重新打开开关时立刻就有可用数据。
        checked_counts: dict[str, int] = {}
        valid_counts: dict[str, int] = {}
        for proxy in checked:
            for source in proxy.sources:
                checked_counts[source] = checked_counts.get(source, 0) + 1
                if proxy.last_status:
                    valid_counts[source] = valid_counts.get(source, 0) + 1
        if checked_counts:
            self.store.record_source_outcomes(checked_counts, valid_counts)
            if history:
                ranked = sorted(
                    (
                        (sampling.smoothed_rate(row["checked"], row["valid"], prior=prior, strength=strength), name)
                        for name, row in history.items()
                    ),
                    reverse=True,
                )
                logger.debug("源权重：%s", ", ".join(f"{name}={rate:.2f}" for rate, name in ranked[:8]))

        # 补全放在入库之前：一次写入就带上地域和匿名度，省去二次回写。
        # 补全失败不影响入库——这两项只是展示字段，缺了还能下轮再补。
        try:
            report.enriched = await self.enrich(valid)
        except Exception as exc:
            logger.warning("元数据补全失败：%s", exc)

        report.stored = self.store.put_many(valid)
        report.elapsed = time.perf_counter() - started
        logger.info(report.summary())
        return report

    # ------------------------------------------------------------------ 元数据
    async def enrich(self, proxies: list[Proxy], *, limit: int | None = None) -> dict[str, int]:
        """补全 ``region``（中文国家名）与 ``anonymous``（匿名度）。

        只处理校验通过的代理：失效代理补了也是白补。两项都只查缺失的，
        已填过的直接跳过，因此重复调用几乎零开销。

        ``limit`` 缺省取 ``META_MAX_PER_ROUND``，防止单轮新增几千条可用代理时
        把匿名度探测（每个代理一次回显请求）拖成新的瓶颈。
        """
        budget = self.settings.meta_max_per_round if limit is None else limit
        targets = [p for p in proxies if p.last_status]
        if not targets:
            return {"region": 0, "anonymous": 0}

        # 先按「缺什么」筛，再截断。顺序反过来会让每轮都只处理同一批：
        # 截断拿到的前 N 条里可能一大半已经有值，剩下的永远轮不到。
        need_region = [p for p in targets if not p.region] if self.settings.proxy_region else []
        need_anon = [p for p in targets if not p.anonymous] if self.settings.proxy_anonymity else []
        if budget > 0:
            need_region = need_region[:budget]
            need_anon = need_anon[:budget]

        filled = {"region": 0, "anonymous": 0}

        # 两项各自兜底：补全是锦上添花，任何失败都不该让抓取/巡检整轮报废
        if need_region:
            try:
                async with build_session(limit=16) as session:
                    filled["region"] = await region.lookup_many([(p.ip, p) for p in need_region], session)
            except Exception as exc:  # noqa: BLE001
                logger.warning("地域补全失败：%s", exc)

        if need_anon:
            urls = (self.settings.anonymity_url,) if self.settings.anonymity_url else None
            try:
                filled["anonymous"] = await anonymity.detect_many(
                    need_anon,
                    concurrency=self.settings.meta_concurrency,
                    urls=urls,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("匿名度探测失败：%s", exc)

        return filled

    async def enrich_pool(self, *, limit: int | None = None) -> dict[str, int]:
        """给存量代理补全元数据（CLI ``enrich`` 使用）。

        会读出全池 JSON，10 万级池子请配合 ``limit`` 分批执行。
        """
        proxies = [Proxy.from_json(raw) for raw in self.store.all(scheme="all")]
        # 打散顺序：探测失败的代理仍然「缺值」，固定顺序会让每轮都从同一批开始，
        # 后面的一辈子轮不到。随机起点保证多跑几轮就能覆盖全池。
        random.shuffle(proxies)
        snapshot = [(p.region, p.anonymous) for p in proxies]
        filled = await self.enrich(proxies, limit=limit)
        # 只回写真正变化的条目：全量 put_many 会把巡检队列整体重置到队尾
        changed = [p for p, before in zip(proxies, snapshot, strict=True) if (p.region, p.anonymous) != before]
        if changed:
            self.store.put_many(changed)
        return filled

    # ------------------------------------------------------------------ 出仓检测
    async def pick_verified(
        self,
        *,
        https: bool = False,
        scheme: str = "http",
        pop: bool = False,
        verify: bool = True,
    ) -> Proxy | None:
        """出仓检测：取出的代理先实时校验，可用才返回；失败自动换下一条。

        - ``get`` 路径：失败的候选按巡检同一套语义处理——超过 ``MAX_FAIL_COUNT``
          就删除，否则摘出索引留在 hash 里等巡检复活（``put_many`` 对
          ``last_status=False`` 的代理不会放进对外索引）。
        - ``pop`` 路径：候选已被 ``store.pop`` 从存储中移除，校验失败等于直接淘汰。
        - 总预算 ``GET_VERIFY_TOTAL`` 兜底：所有尝试共享一个事件循环级超时，
          绝不让 HTTP 请求悬挂。
        """
        fetch = self.store.pop if pop else self.store.get
        if not (verify and self.settings.get_verify):
            payload = fetch(https, scheme)
            return Proxy.from_json(payload) if payload else None

        attempts = max(1, self.settings.get_verify_attempts)
        try:
            async with asyncio.timeout(self.settings.get_verify_total):
                async with build_session(limit=4) as session:
                    for attempt in range(1, attempts + 1):
                        payload = fetch(https, scheme)
                        if not payload:
                            return None
                        proxy = Proxy.from_json(payload)
                        checked = await self.validator.validate(proxy, session)
                        if checked.last_status:
                            if not pop:
                                # 回写最新状态，并把巡检队列排到队尾（刚校验过）
                                self.store.put_many([checked])
                            logger.info("出仓校验通过：%s（第 %d 次尝试）", checked.proxy, attempt)
                            return checked
                        if not pop:
                            if checked.fail_count > self.settings.max_fail_count:
                                self.store.delete(checked.proxy, checked.scheme)
                            else:
                                self.store.put_many([checked])
                        logger.info(
                            "出仓校验失败：%s（fail_count=%d，第 %d/%d 次），换下一条",
                            checked.proxy,
                            checked.fail_count,
                            attempt,
                            attempts,
                        )
        except TimeoutError:
            logger.warning("出仓校验总预算 %.1fs 用尽，放弃本次取用", self.settings.get_verify_total)
        return None

    # ------------------------------------------------------------------ 巡检
    async def check(self, *, refetch_if_low: bool = True, limit: int | None = None) -> CheckReport:
        """增量巡检：每轮只取最久未校验的 N 条，耗时与池大小无关。

        ``limit`` 缺省取 ``CHECK_MAX_PER_ROUND``；``limit<=0`` 表示全池复检
        （仅供手工排障，10 万级池子会跑很久）。
        """
        started = time.perf_counter()
        report = CheckReport()

        # 用 grand_total（HTTP + SOCKS5）：count()["total"] 只统计 HTTP，
        # 纯 SOCKS5 池会被误判为空池而反复触发抓取
        pool = self.store.count()["grand_total"]
        report.pool = pool
        if refetch_if_low and pool < self.settings.pool_size_min:
            logger.info("代理池低于 %d，先执行一次抓取", self.settings.pool_size_min)
            await self.fetch()
            report.refetched = True
            pool = report.pool = self.store.count()["grand_total"]

        batch_limit = self.settings.check_max_per_round if limit is None else limit
        if batch_limit <= 0:
            proxies = [Proxy.from_json(raw) for raw in self.store.all(scheme="all")]
            if proxies:  # 全池复检后重建队列顺序
                self.store.put_many(proxies)
        else:
            proxies = self.store.check_batch(batch_limit)

        if not proxies:
            report.elapsed = time.perf_counter() - started
            logger.info(report.summary())
            return report

        checked = await self.validator.validate_many(proxies)
        report.checked = len(checked)

        survivors: list[Proxy] = []
        removed = 0
        for proxy in checked:
            if proxy.last_status:
                survivors.append(proxy)
                continue
            if proxy.fail_count > self.settings.max_fail_count:
                self.store.delete(proxy.proxy, proxy.scheme)
                removed += 1
            else:
                survivors.append(proxy)

        report.valid = sum(1 for p in survivors if p.last_status)
        report.removed = removed
        report.kept = len(survivors)

        # 巡检顺带补漏：升级前入库的老数据没有地域/匿名度，每轮只补一小批，
        # 避免把 2 分钟的巡检周期撑破（已填过的会在 enrich 里被直接跳过）。
        alive = [p for p in survivors if p.last_status]
        if alive and (self.settings.proxy_region or self.settings.proxy_anonymity):
            try:
                await self.enrich(alive, limit=min(50, self.settings.meta_max_per_round or 50))
            except Exception as exc:
                logger.warning("巡检元数据补全失败：%s", exc)

        self.store.put_many(survivors)

        report.elapsed = time.perf_counter() - started
        logger.info(report.summary())
        return report

    # ------------------------------------------------------------------ 查询
    def stats(self, *, detail: bool = False) -> dict[str, object]:
        data: dict[str, object] = self.store.count()
        if detail:
            data["source"] = self.store.source_stats()
            # 累计可用率：加权采样的依据，也是判断「哪些源不值得再抓」的唯一凭证
            data["source_history"] = {
                name: {
                    **row,
                    "rate": round(
                        sampling.smoothed_rate(
                            row["checked"],
                            row["valid"],
                            prior=self.settings.source_weight_prior,
                            strength=self.settings.source_weight_strength,
                        ),
                        4,
                    ),
                }
                for name, row in self.store.source_history().items()
            }
        return data

    def seed(self, proxies: Iterable[str], source: str = "manual") -> int:
        """手工导入代理（CLI ``import`` 使用），不做校验。"""
        items = [Proxy(p, sources={source}) for p in proxies if p]
        return self.store.put_many(items)
