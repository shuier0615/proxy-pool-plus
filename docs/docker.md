# Docker 部署

## 使用官方镜像

```console
docker pull jhao104/proxy_pool:latest

docker run -d --name proxy_pool \
  --env DB_CONN=redis://redis-host:6379/0 \
  -p 5010:5010 \
  jhao104/proxy_pool:latest
```

`DB_CONN` 等环境变量会覆盖 `setting.py` 中的默认配置，完整清单见 [配置](configuration.md)。
注意无密码 Redis 要写 `redis://host:6379/0`，不要写成 `redis://:pwd@host:6379/0`（会触发 AUTH）。

## 使用 docker compose

项目根目录的 `docker-compose.yml` 同时启动 ProxyPool 与 Redis：

```yaml
services:
  proxy_pool:
    build: .
    ports:
      - "5010:5010"
    environment:
      DB_CONN: "redis://proxy_redis:6379/0"
      TZ: "Asia/Shanghai"
      CHECK_CONCURRENCY: "200"
      WEB_WORKERS: "4"
    depends_on:
      proxy_redis:
        condition: service_healthy
    restart: unless-stopped

  proxy_redis:
    image: "redis:7-alpine"
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
    restart: unless-stopped
```

```console
docker compose up -d --build     # 首次构建并启动
docker compose logs -f proxy_pool
docker compose ps
docker compose down              # 停止并移除容器（保留 redis_data 卷）
```

旧版 `docker-compose`（v1）把命令中的空格换成连字符即可。

## 容器内启动方式

容器使用前台模式（PID 1 为 `tini`，负责转发信号，保证优雅退出），脚本同时拉起
**API** 与**调度器**两个进程：

```console
./proxy_pool.sh start --fg
```

```dockerfile
ENTRYPOINT ["tini", "--", "bash", "proxy_pool.sh", "start", "--fg"]
```

API 优先使用 `gunicorn`（多 worker），未安装时自动回退到 Flask 内置服务器：

```console
gunicorn --workers 4 --bind 0.0.0.0:5010 --timeout 30 api.app:app
```

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WEB_WORKERS` | `4` | gunicorn worker 数，`2×CPU+1` 左右较合适 |
| `WEB_TIMEOUT` | `30` | 单请求超时（秒） |

容器以非 root 用户 `app`（uid 100）运行，只对 `/app` 有写权限。
停止容器时 `tini → bash → gunicorn/scheduler` 逐级转发 `SIGTERM`，实测约 1s 内退出、退出码 0。

## 健康检查

镜像内置 healthcheck，探测 `/health`（Redis 不可用时返回 503）：

```console
docker inspect --format '{{json .State.Health}}' proxy_pool
```

Redis 故障期间所有接口统一返回 `503 {"code":503,"msg":"redis unavailable"}`，
便于负载均衡/编排系统直接摘流量；Redis 恢复后连接池自动重连，无需重启容器。

## 构建说明

`.dockerignore` 已排除 `.venv/`、缓存、日志与测试产物，构建上下文仅数百 KB。
`lxml` 在 alpine 上需要 `gcc/musl-dev/libxml2-dev/libxslt-dev` 现场编译，
首次构建约 4 分钟（之后命中缓存只需数秒），编译依赖在安装完成后即被删除，
最终镜像约 146MB。

## 资源建议

| 代理池规模 | 建议内存 | 建议 CPU | 说明 |
| --- | --- | --- | --- |
| < 5k | 256MB | 1 核 | 单轮抓取约 1 分钟 |
| 5k ~ 2w | 512MB | 2 核 | 适当调大 `CHECK_CONCURRENCY` |
| > 2w | 1GB | 4 核 | 建议 API 与 scheduler 拆分为两个容器 |

抓取与巡检是 I/O 密集任务，CPU 主要消耗在 lxml 解析上；
容器内请确认 `nofile` 上限（`docker run --ulimit nofile=8192:8192`）。

## 常见问题

**容器起来了但池子一直是空的**
看日志是否有 `开始抓取`：调度器启动即抓一轮，约 1~2 分钟后 `/count` 才会有数据；
若 `DB_CONN` 指向错误主机，`/health` 会返回 503 并提示 Redis 不可用。

**宿主机能连 Redis，容器里连不上**
容器内的 `127.0.0.1` 是容器自身，连宿主机 Redis 要用宿主机内网 IP，或给容器加
`--add-host=host.docker.internal:host-gateway` 后使用 `host.docker.internal`；
同时确认 Redis 未开启 `protected-mode` 拒绝外部连接。
