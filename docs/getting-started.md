# 快速开始

## 环境要求

- Python 3.12 或 3.13
- Redis 5.0+

## 1. 获取代码

```console
git clone https://github.com/jhao104/proxy_pool.git
cd proxy_pool
```

## 2. 启动 Redis

```console
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

## 3. 安装依赖

```console
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 4. 配置

配置项全部支持环境变量，常用项：

```bash
# 默认即无密码本地实例（redis://127.0.0.1:6379/0），一般无需设置
export DB_CONN="redis://:password@127.0.0.1:6379/0"   # 有密码时这样写
export PORT=5010
```

完整清单见 [配置](configuration.md)。

## 5. 启动

```console
# 终端 1：API 服务
python proxyPool.py server

# 终端 2：定时抓取 + 巡检
python proxyPool.py schedule
```

若只想先验证效果，可执行一次抓取：

```console
python proxyPool.py fetch --max-new 300
python proxyPool.py stats -d
```

首次抓取典型输出：

```text
源 monosans       获取 1524 个
源 thespeedx      获取 2896 个
...
校验完成：300 个代理，可用 58 个（并发 200）
抓取完成：14 个源 / 原始 3039 / 待校验 300 / 可用 58 / 入库 58 / 耗时 35.0s
```

## 6. 验证

```console
curl http://127.0.0.1:5010/count
curl "http://127.0.0.1:5010/get?format=text"
```

## 常见问题

**启动报 `Redis 连接失败`**
检查 `DB_CONN` 是否正确、Redis 是否放行当前 IP；`/health` 会返回 `503`。

**抓取到很多代理但可用数很少**
免费代理本身可用率普遍在 10%~30%。可调整 `HTTP_URL`/`HTTPS_URL` 为国内可直连的目标，
或提高 `MAX_NEW_PROXIES` 增加样本量。

**校验很慢**
`VERIFY_TIMEOUT=8` 意味着死代理最坏占用 8 秒；200 并发下 5000 条约 3~5 分钟。
可下调 `VERIFY_TIMEOUT` 或 `MAX_NEW_PROXIES`。

**从 2.x 升级**
旧数据仍在 `{TABLE_NAME}` hash 中，`RedisStore` 首次读取会自动重建索引；
也可手动执行 `python proxyPool.py reindex`。
