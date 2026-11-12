# API 使用

启动 `python proxyPool.py server` 后，默认监听 `http://127.0.0.1:5010`。

| API | Method | 说明 | 参数 |
| --- | --- | --- | --- |
| `/` | GET | 接口列表与版本 | - |
| `/get` | GET | 随机获取一个代理 | `?type=https` 仅 HTTPS；`?type=socks5` 取 SOCKS5；`?format=text` 返回纯文本；`?verify=0` 跳过出仓检测 |
| `/pop` | GET | 获取并删除一个代理 | `?type=https`、`?type=socks5`；`?verify=0` 跳过出仓检测 |
| `/all` | GET | 获取全部代理 | `?type=https`、`?type=socks5`、`?type=all`、`?limit=100` |
| `/count` | GET | 数量统计 | `?detail=1` 附带按来源分布 |
| `/delete` | GET | 删除指定代理 | `?proxy=1.2.3.4:8080&type=socks5` |
| `/sources` | GET | 当前启用的代理源 | - |
| `/health` | GET | 健康检查 | - |

## 响应示例

```bash
$ curl http://127.0.0.1:5010/get
{
  "proxy": "110.38.233.122:8080",
  "scheme": "http",
  "https": true,
  "fail_count": 0,
  "region": "中国",
  "anonymous": "high_anonymous",
  "source": "monosans/thespeedx",
  "check_count": 2,
  "last_status": true,
  "last_time": "2026-11-04 01:37:02"
}

$ curl "http://127.0.0.1:5010/get?format=text"
110.38.233.122:8080

$ curl http://127.0.0.1:5010/get?type=socks5
{
  "proxy": "45.74.31.47:4143",
  "scheme": "socks5",
  "https": false,
  "source": "proxyscrape_s5"
}

$ curl http://127.0.0.1:5010/count
{"count": 24, "http": 17, "https": 7,
 "socks5": 29, "socks5_https": 18, "total": 53,
 "available": 47, "available_total": 53,
 "http_type": {"http": 17, "https": 7}}

$ curl http://127.0.0.1:5010/count?detail=1
{"count": 24, "http": 17, "https": 7,
 "socks5": 29, "socks5_https": 18, "total": 53,
 "http_type": {"http": 17, "https": 7},
 "source": {"monosans": 15, "thespeedx": 9, "proxyscrape_s5": 20, "ip89": 3},
 "source_history": {
   "ip89": {"checked": 1200, "valid": 384, "rate": 0.3214},
   "monosans": {"checked": 5000, "valid": 275, "rate": 0.0568}}}
```

> `count` 字段为 HTTP 代理总数（保持旧版语义），`total` 为 HTTP + SOCKS5 合计。

## 字段说明

| 字段 | 说明 |
| --- | --- |
| `proxy` | 裸 `ip:port`，不含协议前缀 |
| `scheme` | `http` 或 `socks5` |
| `https` | 是否支持 HTTPS（CONNECT 隧道） |
| `region` | 出口 IP 的中文国家/地区名，如 `中国`、`美国`、`中国香港` |
| `anonymous` | 匿名度，见下表 |
| `source` | 来源标识，多源以 `/` 分隔 |
| `fail_count` | 连续失败计数，成功一次 -1 |
| `check_count` | 累计校验次数 |
| `last_status` | 最近一次校验是否可用 |
| `last_time` | 最近一次校验时间 |

`anonymous` 取值：

| 值 | 含义 |
| --- | --- |
| `transparent` | 透明代理：目标站能看到你的真实 IP（`X-Forwarded-For` 等被转发，或 `origin` 是多跳链路） |
| `anonymous` | 普通匿名：真实 IP 被隐藏，但留下代理痕迹（`Via` / `Proxy-Connection` / 代理自身 IP） |
| `high_anonymous` | 高匿：既无真实 IP 也无代理痕迹 |
| `unknown` | 尚未探测或探测失败 |

`region` 与 `anonymous` 的默认值（`未知` / `unknown`）**只在接口输出时补上**，
Redis 里依旧存空串——否则「没探测过」和「探测失败」就分不清，补全任务也不会再重试。
两者由 `PROXY_REGION` / `PROXY_ANONYMITY` 控制，抓取与巡检时会自动补全，
也可以执行 `python proxyPool.py enrich` 一次性补齐存量数据。
> `available` / `available_total` 是**当前真正可对外提供**的数量：`MAX_FAIL_COUNT`
> 容错让失败一次、等待复检复活的代理仍留在池内，但不会再被 `/get` 返回，
> 因此 `available` 可能小于 `count`。
> `source` 是当前池内的来源分布，`source_history` 是各源累计校验战绩
> （`rate` 为带先验平滑后的可用率），也是加权采样的权重来源。
> `/delete` 不带 `type` 时会依次尝试两种命名空间，返回 `scheme` 标明实际删除的协议。

无可用代理时，`/get` 与 `/pop` 返回 HTTP 404 与 `{"code": 0, "src": "no proxy"}`，
便于爬虫侧据此触发等待或自建回退逻辑。

## 出仓检测

`/get` 与 `/pop` **返回前先实时校验**：从索引取出候选 → 立即探测（与巡检同一套
校验器）→ 可用才返回；失败自动换下一条，最多尝试 `GET_VERIFY_ATTEMPTS` 条，
全部尝试共享 `GET_VERIFY_TOTAL` 秒的总预算，超时同样返回 404。

- 校验失败的候选按巡检同一套语义处理：超过 `MAX_FAIL_COUNT` 直接删除，
  否则下架（摘出索引、留在池内等待巡检复活），不会发给下一个客户端。
- 出仓检测会让 `/get` 的响应时间从毫秒级变为「一次探测的耗时」（通常
  0.3~2s，最坏情况受总预算约束）。对延迟敏感的场景可加 `?verify=0` 跳过，
  恢复旧行为（直接从索引随机取，不保证当场可用）。
- 全局开关：`GET_VERIFY=false`。

## 在爬虫中使用

```python
import httpx

BASE = "http://127.0.0.1:5010"


def get_proxy() -> str:
    return httpx.get(f"{BASE}/get?format=text", timeout=5).text


def delete_proxy(proxy: str) -> None:
    httpx.get(f"{BASE}/delete", params={"proxy": proxy}, timeout=5)


def fetch(url: str) -> str | None:
    proxy = get_proxy()
    if not proxy:
        return None
    try:
        return httpx.get(url, proxy=f"http://{proxy}", timeout=10).text
    except httpx.HTTPError:
        delete_proxy(proxy)
        return None
```

`requests` 用法相同，只需把 `proxy=f"http://{proxy}"` 换成
`proxies={"http": f"http://{proxy}", "https": f"http://{proxy}"}`。

## 生产部署

```bash
gunicorn -w 4 -b 0.0.0.0:5010 "api.app:app"
```

API 进程本身不做抓取，只读写 Redis，可水平扩展；抓取与巡检由独立的
`python proxyPool.py schedule` 进程负责。

## SOCKS5 代理

SOCKS5 与 HTTP 代理使用独立命名空间，接口通过 `type=socks5` 区分：

```bash
# 取一个 SOCKS5 代理
curl "http://127.0.0.1:5010/get?type=socks5&format=text"

# 爬虫侧用法（httpx 需要额外依赖 httpx[socks]）
import httpx
proxies = f"socks5://{proxy}"
httpx.get(url, proxy=proxies)

# requests 需 requests[socks]
requests.get(url, proxies={"http": proxies, "https": proxies})
```

同一 `host:port` 可能同时存在 HTTP 与 SOCKS5 两条记录，删除时建议带上 `type`。
