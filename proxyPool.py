"""ProxyPool 命令行入口。

python proxyPool.py server          启动 API
python proxyPool.py schedule        启动调度器
python proxyPool.py fetch           执行一次抓取（--source 可指定源）
python proxyPool.py check           执行一次巡检
python proxyPool.py enrich          补全存量代理的地域与匿名度
python proxyPool.py fetcher         查看启用的代理源
python proxyPool.py stats           查看池状态
python proxyPool.py reindex         重建索引
python proxyPool.py clear           清空代理池
python proxyPool.py import FILE     导入本地代理清单
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import click

import setting
from config import get_settings
from logging_setup import get_logger, setup_logging
from utils import runtime, sampling
from utils.proxy_parse import parse_proxies

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


def _run(coro):
    """统一的事件循环入口（带异常降噪）。"""
    return runtime.run(coro)


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(version=setting.VERSION, prog_name="proxyPool")
def cli() -> None:
    """ProxyPool 代理池管理工具"""


@cli.command()
def server() -> None:
    """启动 Web API 服务"""
    click.echo(setting.BANNER)
    setup_logging()
    log = get_logger("launcher")
    conf = get_settings()
    log.info("ProxyPool %s 启动 API：http://%s:%s", setting.VERSION, conf.host, conf.port)
    from api.app import run_server

    run_server()


def _service(conf=None):
    """构造 service 并检查 Redis 连通性，失败时给出可读提示。"""
    from service import ProxyPoolService

    service = ProxyPoolService(settings=conf)
    if not service.store.ping():
        raise click.ClickException(f"无法连接 Redis，请检查网络/DB_CONN 配置（当前：{service.settings.db_conn}）")
    return service


@cli.command()
def schedule() -> None:
    """启动定时抓取与巡检"""
    click.echo(setting.BANNER)
    setup_logging()
    from scheduler import run_scheduler

    run_scheduler()


@cli.command()
@click.option("-s", "--source", "sources", multiple=True, help="只抓取指定源（可多次指定）")
@click.option("-c", "--concurrency", type=int, default=None, help="抓取并发数")
@click.option("--per-source", type=int, default=None, help="每个源最多保留多少条")
@click.option("--max-new", type=int, default=None, help="本轮最多校验多少条新代理")
def fetch(sources: tuple[str, ...], concurrency: int | None, per_source: int | None, max_new: int | None) -> None:
    """执行一次抓取 + 校验 + 入库"""
    setup_logging()
    conf = get_settings()
    if concurrency:
        conf = dataclasses.replace(conf, fetch_concurrency=concurrency)

    service = _service(conf)
    report = _run(service.fetch(sources or None, max_per_source=per_source, max_new=max_new))

    for name, count in sorted(report.per_source.items(), key=lambda kv: kv[1], reverse=True):
        click.echo(f"  {name:<16} {count:>6}")
    if report.errors:
        click.echo("  失败源：" + ", ".join(f"{k}({v})" for k, v in report.errors.items()))
    click.echo(report.summary())


@cli.command()
@click.option("--no-refetch", is_flag=True, help="池子不足时不触发抓取")
@click.option("-n", "--limit", type=int, default=None, help="本轮最多复检多少条（0=全池）")
def check(no_refetch: bool, limit: int | None) -> None:
    """巡检池内代理，剔除失效项（增量：最久未检优先）"""
    setup_logging()

    report = _run(_service().check(refetch_if_low=not no_refetch, limit=limit))
    click.echo(report.summary())


@cli.command()
@click.option("-n", "--limit", type=int, default=None, help="本轮最多补全多少条（0=不限制）")
def enrich(limit: int | None) -> None:
    """补全存量代理的地域与匿名度"""
    setup_logging()
    filled = _run(_service().enrich_pool(limit=limit))
    click.echo(f"元数据补全完成：地域 {filled['region']} 条，匿名度 {filled['anonymous']} 条")


@cli.command(name="fetcher")
def list_fetchers() -> None:
    """列出当前启用的代理源"""
    conf = get_settings()
    from fetcher.registry import discover_sources

    classes = discover_sources(conf.fetcher_exclude)
    click.echo(f"启用的代理源（{len(classes)}）:")
    for cls in classes:
        click.echo(f"  {cls.name:<16} {cls.url}")
    if conf.fetcher_exclude:
        click.echo("\n已排除: " + ", ".join(sorted(conf.fetcher_exclude)))


@cli.command()
@click.option("-d", "--detail", is_flag=True, help="包含按来源统计")
def stats(detail: bool) -> None:
    """查看代理池状态"""
    data = _service().stats(detail=detail)
    click.echo(
        f"HTTP: {data['total']}（其中支持 HTTPS {data['https']}）  "
        f"SOCKS5: {data['socks5']}（其中支持 HTTPS {data['socks5_https']}）  "
        f"合计: {data['grand_total']}"
    )
    source = data.get("source") or {}
    if source:
        click.echo("\n池内来源分布:")
        for name, count in source.items():
            click.echo(f"  {name:<16} {count:>6}")

    history = data.get("source_history") or {}
    if history:
        click.echo("\n源累计可用率（加权采样的依据）:")
        rows = sorted(history.items(), key=lambda kv: kv[1]["rate"], reverse=True)
        for name, row in rows:
            click.echo(
                f"  {name:<16} {row['rate']:>6.1%}  "
                f"({row['valid']}/{row['checked']}，权重 {max(row['rate'], sampling.MIN_WEIGHT):.3f})"
            )


@cli.command()
def reindex() -> None:
    """重建 Redis 索引（升级或数据迁移后执行一次）"""
    result = _service().store.reindex()
    click.echo(
        f"索引重建完成: HTTP={result['http']}（https={result['http_https']}）  "
        f"SOCKS5={result['socks5']}（https={result['socks5_https']}）  合计={result['total']}"
    )


@cli.command()
@click.option("--yes", is_flag=True, help="确认清空")
@click.option("--stats", "with_stats", is_flag=True, help="同时清除源可用率统计")
def clear(yes: bool, with_stats: bool) -> None:
    """清空代理池（危险操作）"""
    if not yes:
        raise click.UsageError("该操作会删除全部代理，请追加 --yes 确认")
    store = _service().store
    store.clear()
    if with_stats:
        store.clear_source_stats()
        click.echo("代理池与源可用率统计已清空")
    else:
        click.echo("代理池已清空（源可用率统计保留，如需重置请加 --stats）")


@cli.command(name="import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--source", default="manual", help="来源标记")
def import_file(path: Path, source: str) -> None:
    """导入本地代理清单（每行 ip:port）"""
    proxies = parse_proxies(path.read_text(encoding="utf-8", errors="ignore"))
    if not proxies:
        raise click.ClickException("文件中未解析到有效代理")
    count = _service().seed(proxies, source=source)
    click.echo(f"已导入 {count} 个代理")


if __name__ == "__main__":
    cli()
