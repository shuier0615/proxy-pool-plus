"""HTTP API（Flask 应用工厂）。

接口保持向后兼容，同时新增：

* ``/get?format=text``        直接返回 ``ip:port`` 纯文本，方便 curl 管道使用
* ``/count?detail=1``         附带按来源统计
* ``/health``                 存活探针（含 Redis 连通性）
* ``/sources``                当前启用的代理源列表
"""

from __future__ import annotations

import asyncio

from flask import Flask, Response, jsonify, request
from redis.exceptions import RedisError

import setting
from checker import anonymity, region
from config import get_settings
from fetcher.registry import discover_sources
from logging_setup import get_logger
from models import Proxy
from service import ProxyPoolService

logger = get_logger("api")

API_LIST = [
    {
        "url": "/get",
        "params": "type: ''https'' | ''socks5'' | ''; format: ''text''; verify: 0|1",
        "desc": "随机获取一个可用代理（默认出仓检测，verify=0 跳过）",
    },
    {
        "url": "/pop",
        "params": "type: ''https'' | ''socks5'' | ''; verify: 0|1",
        "desc": "获取并移除一个代理（默认出仓检测）",
    },
    {"url": "/all", "params": "type: ''https'' | ''socks5'' | ''all''; limit: int", "desc": "获取全部代理"},
    {"url": "/count", "params": "detail: 0 | 1", "desc": "代理数量统计（含 socks5）"},
    {"url": "/delete", "params": "proxy: 'ip:port'; type: ''socks5''", "desc": "删除指定代理"},
    {"url": "/sources", "params": "", "desc": "当前启用的代理源"},
    {"url": "/health", "params": "", "desc": "健康检查"},
]


def _proxy_type() -> str:
    """解析 ``type`` 参数：socks5 / all / 其他（按 HTTP 处理）。"""
    raw = request.args.get("type", "").strip().lower()
    if raw in {"socks5", "socks"}:
        return "socks5"
    if raw == "all":
        return "all"
    return "http"


def _wants_https() -> bool:
    return request.args.get("type", "").lower() == "https"


def _wants_verify() -> bool:
    """``verify=0`` 可跳过出仓检测（旧行为：直接取索引，不保证当场可用）。"""
    return request.args.get("verify", "1").strip().lower() not in {"0", "false", "no", "off"}


def _present(proxy: Proxy) -> dict[str, object]:
    """代理的对外字典：地域/匿名度缺失时补默认值。

    存储层刻意保持空串——写死默认值会让「未探测」和「探测失败」无法区分，
    补全任务也就再也不会重试。所以兜底只发生在输出这一层。
    """
    data = proxy.to_dict()
    data["region"] = data["region"] or region.DEFAULT_REGION
    data["anonymous"] = data["anonymous"] or anonymity.DEFAULT_ANONYMOUS
    return data


def create_app(service: ProxyPoolService | None = None) -> Flask:
    app = Flask(__name__)
    app.url_map.strict_slashes = False
    app.json.ensure_ascii = False
    app.json.sort_keys = False
    svc = service or ProxyPoolService()
    settings = get_settings()

    @app.get("/")
    def index():
        return {"version": setting.VERSION, "url": API_LIST}

    @app.get("/get")
    def get_proxy():
        proxy = asyncio.run(svc.pick_verified(https=_wants_https(), scheme=_proxy_type(), verify=_wants_verify()))
        if not proxy:
            return jsonify({"code": 0, "src": "no proxy"}), 404

        if request.args.get("format", "").lower() == "text":
            return Response(proxy.proxy, mimetype="text/plain")
        return _present(proxy)

    @app.get("/pop")
    def pop_proxy():
        proxy = asyncio.run(
            svc.pick_verified(https=_wants_https(), scheme=_proxy_type(), pop=True, verify=_wants_verify())
        )
        if not proxy:
            return jsonify({"code": 0, "src": "no proxy"}), 404

        if request.args.get("format", "").lower() == "text":
            return Response(proxy.proxy, mimetype="text/plain")
        return _present(proxy)

    @app.get("/all")
    def all_proxies():
        proxies = [Proxy.from_json(raw) for raw in svc.store.all(_wants_https(), _proxy_type())]
        limit = request.args.get("limit", type=int)
        if limit and limit > 0:
            proxies = proxies[:limit]
        return jsonify([_present(p) for p in proxies])

    @app.get("/count")
    def count():
        stats = svc.stats(detail=request.args.get("detail", "0") in {"1", "true", "yes"})
        total = int(stats.get("total", 0))
        https = int(stats.get("https", 0))
        socks5 = int(stats.get("socks5", 0))
        socks5_https = int(stats.get("socks5_https", 0))
        body: dict[str, object] = {
            "count": total,
            "http": total - https,
            "https": https,
            "socks5": socks5,
            "socks5_https": socks5_https,
            "total": total + socks5,
            # 校验失败一次、等待复活的代理仍在池内但不会发给客户端
            "available": int(stats.get("available", 0)),
            "available_total": int(stats.get("available_total", 0)),
            # 兼容旧版字段
            "http_type": {"http": total - https, "https": https},
        }
        for key in ("source", "source_history"):
            if key in stats:
                body[key] = stats[key]
        return body

    @app.get("/delete")
    def delete():
        proxy = request.args.get("proxy", "").strip()
        if not proxy:
            return jsonify({"code": 1, "msg": "missing query parameter: proxy"}), 400
        # 未显式指定 type 时两种命名空间都尝试，兼容同时存在同名 HTTP/SOCKS5 代理
        if request.args.get("type"):
            removed = svc.store.delete(proxy, _proxy_type())
            return {"code": 0, "src": removed, "scheme": _proxy_type() if removed else ""}
        hit = svc.store.delete_any(proxy)
        return {"code": 0, "src": bool(hit), "scheme": "/".join(hit)}

    @app.get("/sources")
    def sources():
        classes = discover_sources(settings.fetcher_exclude)
        return jsonify(
            [
                {
                    "name": cls.name,
                    "url": cls.url,
                    "scheme": cls.scheme,
                    "protocols": list(cls.protocols),
                }
                for cls in classes
            ]
        )

    @app.get("/health")
    def health():
        ok = svc.store.ping()
        return ({"status": "ok" if ok else "degraded", "redis": ok, "version": setting.VERSION}, 200 if ok else 503)

    @app.errorhandler(RedisError)
    def redis_unavailable(exc: RedisError):
        """Redis 故障时返回 503，而不是笼统的 500，便于负载均衡摘流量。"""
        logger.error("Redis 不可用: %s", exc)
        return jsonify({"code": 503, "msg": "redis unavailable"}), 503

    @app.errorhandler(404)
    def not_found(_):
        return jsonify({"code": 404, "msg": "not found", "url": API_LIST}), 404

    @app.errorhandler(500)
    def server_error(exc):
        logger.exception("接口异常: %s", exc)
        return jsonify({"code": 500, "msg": "internal server error"}), 500

    return app


app = create_app()


def run_server() -> None:
    """开发/小规模部署启动入口。生产环境建议使用 gunicorn。"""
    settings = get_settings()
    app.run(host=settings.host, port=settings.port, threaded=True)


if __name__ == "__main__":  # pragma: no cover
    run_server()
