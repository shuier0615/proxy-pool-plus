[![Tests](https://github.com/jhao104/proxy_pool/actions/workflows/test.yml/badge.svg)](https://github.com/jhao104/proxy_pool/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/jhao104/proxy_pool/graph/badge.svg?token=8WHGkrQA6E)](https://codecov.io/gh/jhao104/proxy_pool)
[![](https://img.shields.io/badge/Powered%20by-@j_hao104-green.svg)](http://www.spiderpy.cn/blog/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![](https://img.shields.io/badge/python-3.12%20%7C%203.13-green.svg)](https://www.python.org/)

    ______                        ______             _
    | ___ \_                      | ___ \           | |
    | |_/ / \__ __   __  _ __   _ | |_/ /___   ___  | |
    |  __/|  _// _ \ \ \/ /| | | ||  __// _ \ / _ \ | |
    | |   | | | (_) | >  < \ |_| || |  | (_) | (_) || |___
    \_|   |_|  \___/ /_/\_\ \__  |\_|   \___/ \___/ \_____\
                           __ / /
                          /___ /

### ProxyPool

爬虫代理 IP 池：**异步**采集全网公开免费代理 → 校验可用性 → 存入 Redis → 通过 HTTP API / CLI 提供代理。

* 文档：[document](https://jhao104.github.io/proxy_pool/)
* 支持版本：Python 3.12 / 3.13
* 测试地址：http://demo.spiderpy.cn （勿压，谢谢）

* 付费代理推荐：[亮数据 Bright Data](https://get.brightdata.com/github_jh)（前身 Luminati），覆盖 195+ 国家的 1.5 亿+ 真实住宅 IP。[申请免费试用](https://get.brightdata.com/github_jh)

---

## 3.0 版本亮点

| 维度 | 2.x | 3.0 |
| --- | --- | --- |
| 运行时 | Python 3.8+，`requests` + 多线程 | Python 3.12+，`asyncio` + `aiohttp` 连接池 |
| 校验并发 | 固定 20 线程 | 默认 200 并发（可配置），实测 300 条代理 35 秒校验完毕 |
| 巡检方式 | 每轮全池复检，耗时随池子线性增长 | zset 增量队列，最久未检优先，单轮耗时与池大小无关 |
| 校验配额 | 按条数均匀分配 | 按源历史可用率加权（优质源 20%+，劣质大清单 1%），同样 5000 条配额可用数 211 → 469 |
| 随机取用 | `HGETALL` 全量扫描后过滤 | Redis Set 索引 + `SRANDMEMBER`，O(1) |
| 计数 | 全量扫描 | `HLEN` / `SCARD`，O(1) |
| 存储 | Redis + 伪 SSDB 实现 | Redis（删除重复的 SSDB 实现） |
| 代理源 | 13 个（部分已失效） | 36 个（30 HTTP + 6 SOCKS5），GitHub 源自动切换镜像 |
| 代理协议 | 仅 HTTP | HTTP / SOCKS5（独立命名空间，互不覆盖） |
| 代码量 | `util/six.py`、`lazyProperty`、`withMetaclass` 等 PY2 兼容层 | 删除全部兼容层与死代码，全量类型注解 |

---

## 快速开始

### 1. 准备 Redis

```bash
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

### 2. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 修改配置

配置项支持**环境变量覆盖**，无需改动源码：

```bash
# 默认即无密码本地实例（redis://127.0.0.1:6379/0），一般无需设置
export DB_CONN="redis://:password@127.0.0.1:6379/0"   # 有密码时这样写
export PORT=5010
```

完整配置见 [setting.py](setting.py) 与 [docs/configuration.md](docs/configuration.md)。

### 4. 启动

```bash
# 启动 API 服务（默认 http://0.0.0.0:5010）
python proxyPool.py server

# 启动定时抓取 + 巡检
python proxyPool.py schedule
```

### Docker

```bash
docker compose up -d --build
```

---

## 命令行

```text
python proxyPool.py server                     启动 API
python proxyPool.py schedule                   启动调度器
python proxyPool.py fetch                      立即执行一次「抓取 → 校验 → 入库」
python proxyPool.py fetch -s ip89 -s monosans  只跑指定源
python proxyPool.py fetch --max-new 500        本轮最多校验 500 条
python proxyPool.py check                      立即巡检（最久未检的 CHECK_MAX_PER_ROUND 条）
python proxyPool.py check -n 0                 全池复检（排障用，大池子会很慢）
python proxyPool.py enrich                     补全存量代理的地域与匿名度
python proxyPool.py fetcher                    查看启用的代理源
python proxyPool.py stats -d                   查看池状态（来源分布 + 各源累计可用率）
python proxyPool.py reindex                    重建 Redis 索引（升级后执行一次）
python proxyPool.py import proxies.txt         导入本地清单
python proxyPool.py clear --yes                清空代理池（保留源可用率统计，并加 --stats 一并清除）
```

一次典型的 `fetch` 输出：

```text
  monosans           1524
  mmpx12              440
  clarketm            400
  hideip              204
  ...
抓取完成：14 个源 / 原始 3039 / 待校验 300 / 可用 58 / 入库 58 / 耗时 35.0s
```

---

## API

| API | Method | 说明 | 参数 |
| --- | --- | --- | --- |
| `/` | GET | 接口列表与版本 | - |
| `/get` | GET | 随机获取一个代理 | `?type=https` 仅 HTTPS；`?type=socks5` 取 SOCKS5；`?format=text` 返回纯文本 |
| `/pop` | GET | 获取并删除一个代理 | `?type=https` |
| `/all` | GET | 获取全部代理 | `?type=https`、`?limit=100` |
| `/count` | GET | 代理数量统计 | 含 `socks5` / `socks5_https` / `total`；`?detail=1` 附带来源分布 |
| `/delete` | GET | 删除指定代理 | `?proxy=1.2.3.4:8080` |
| `/sources` | GET | 当前启用的代理源 | - |
| `/health` | GET | 健康检查（含 Redis 连通性） | - |

无可用代理时 `/get`、`/pop` 返回 `404` 与 `{"code": 0, "src": "no proxy"}`。

爬虫中直接使用：

```python
import httpx


def get_proxy() -> str:
    resp = httpx.get("http://127.0.0.1:5010/get?format=text", timeout=5)
    return resp.text


def delete_proxy(proxy: str) -> None:
    httpx.get(f"http://127.0.0.1:5010/delete?proxy={proxy}", timeout=5)


proxy = get_proxy()
try:
    html = httpx.get("https://example.com", proxy=f"http://{proxy}", timeout=10).text
except httpx.HTTPError:
    delete_proxy(proxy)
```

生产部署建议使用 gunicorn（POSIX）：

```bash
gunicorn -w 4 -b 0.0.0.0:5010 "api.app:app"
```

---

## 代理源（36 个，均已实测）

### 纯文本清单（GitHub / 公开 API）

| 名称 | 说明 | 实测条数 |
| --- | --- | --- |
| `monosans` | monosans/proxy-list，带存活检测 | ~1500 |
| `thespeedx` | TheSpeedX/PROXY-List | ~2900 |
| `clarketm` | clarketm/proxy-list | ~400 |
| `databay` | databay-labs/free-proxy-list | ~6900 |
| `vakhov` | vakhov/fresh-proxy-list，每小时刷新 | ~520 |
| `iplocate` | iplocate/free-proxy-list | ~900 |
| `openproxylist` | openproxylist.xyz | ~7200 |
| `proxy4parsing` | proxy4parsing/proxy-list | ~19000 |
| `prxchk` | prxchk/proxy-list，5 分钟验活 | ~60 |
| `murongpig` | MuRongPIG/Proxy-Master | ~100000 |
| `zevtyardt` | zevtyardt/proxy-list | ~63000 |
| `aliilapro` | ALIILAPRO/Proxy | ~1300 |
| `hideip` | zloi-user/hideip.me | ~200 |
| `roosterkid` | roosterkid/openproxylist | ~150 |
| `shifty` | ShiftyTR/Proxy-List | ~40 |
| `mmpx12` | mmpx12/proxy-list | ~440 |
| `proxyscrape` | ProxyScrape 全球 HTTP | ~1400 |
| `proxyscrape_cn` | ProxyScrape 中国区 | ~25 |
| `proxyscrape_v2` | ProxyScrape legacy v2 接口，与 v4 仅部分重合 | ~1300 |

### JSON API

| 名称 | 说明 | 实测条数 |
| --- | --- | --- |
| `geonode` | Geonode 实时清单（仅取 `http` 协议） | ~190 |
| `roundproxies` | Roundproxies API（仅取 `http` 协议） | ~30 |
| `scdn` | proxy.scdn.io（仅取 HTTP 行） | ~20 |
| `proxifly` | Proxifly 全量 JSON，按国家/协议过滤 | ~1200 |
| `proxifly_cn` | Proxifly 中国区 JSON，中国代理可用率最高 | ~130 |

### SOCKS5 清单（`scheme=socks5`，独立存储）

| 名称 | 说明 | 实测条数 |
| --- | --- | --- |
| `proxyscrape_s5` | ProxyScrape v2 SOCKS5 接口 | ~4900 |
| `aliilapro_s5` | ALIILAPRO/Proxy SOCKS5 | ~5000 |
| `zevtyardt_s5` | zevtyardt/proxy-list SOCKS5 | ~23000 |
| `monosans_s5` | monosans SOCKS5，带存活检测 | ~1100 |
| `thespeedx_s5` | TheSpeedX/SOCKS-List | ~3100 |
| `mmpx12_s5` | mmpx12/proxy-list SOCKS5 | ~280 |

> SOCKS5 与 HTTP 使用独立 Redis 命名空间（`{table}:s5*`），同一 `host:port` 可同时以两种协议存在。
> 校验需为每个代理单独建立 SOCKS 通道，故单轮校验量由 `SOCKS_MAX_NEW_PROXIES` 单独限制。

### 国内站点

| 名称 | 地址 |
| --- | --- |
| `ip89` | https://www.89ip.cn |
| `ip3366` | http://www.ip3366.net |
| `ihuan` | https://ip.ihuan.me |
| `kxdaili` | http://www.kxdaili.com |
| `goodips` | https://www.goodips.com |
| `freevpnnode` | https://cn.freevpnnode.com |

> 已移除失效源：快代理（JS 反爬）、站大爷（页面结构变更）、稻壳代理（域名不可达）、66代理（API 502）。

新增代理源见 [docs/extending/fetcher.md](docs/extending/fetcher.md)，最简只需 8 行：

```python
from fetcher.base import TextListFetcher


class MyFetcher(TextListFetcher):
    name = "myproxy"
    url = "https://example.com"
    urls = ("https://example.com/proxies.txt",)
```

放入 `fetcher/sources/` 后自动被发现，可用 `python proxyPool.py fetcher` 确认。

---

## 测试

```bash
pip install -r requirements-test.txt
pytest                                   # 单元 + API 测试
pytest --cov=. --cov-report=term-missing # 覆盖率
REDIS_URL=redis://127.0.0.1:6379/0 pytest -m integration  # 集成测试
```

---

## 问题反馈

任何问题欢迎在 [Issues](https://github.com/jhao104/proxy_pool/issues) 中反馈。

## 贡献代码

本项目仅作为通用的代理池架构。发现 bug 或有新功能，请在 Issues 中提交描述。

感谢以下 contributor：[@kangnwh](https://github.com/kangnwh) | [@bobobo80](https://github.com/bobobo80) | [@halleywj](https://github.com/halleywj) | [@newlyedward](https://github.com/newlyedward) | [@wang-ye](https://github.com/wang-ye) | [@gladmo](https://github.com/gladmo) | [@bernieyangmh](https://github.com/bernieyangmh) | [@PythonYXY](https://github.com/PythonYXY) | [@zuijiawoniu](https://github.com/zuijiawoniu) | [@netAir](https://github.com/netAir) | [@scil](https://github.com/scil) | [@tangrela](https://github.com/tangrela) | [@highroom](https://github.com/highroom) | [@luocaodan](https://github.com/luocaodan) | [@vc5](https://github.com/vc5) | [@1again](https://github.com/1again) | [@obaiyan](https://github.com/obaiyan) | [@zsbh](https://github.com/zsbh) | [@jiannanya](https://github.com/jiannanya) | [@Jerry12228](https://github.com/Jerry12228) | [@zeyudada](https://github.com/zeyudada)

## Release Notes

[changelog](https://jhao104.github.io/proxy_pool/changelog/)

<a href="https://hellogithub.com/repository/92a066e658d147cc8bd8397a1cb88183" target="_blank"><img src="https://api.hellogithub.com/v1/widgets/recommend.svg?rid=92a066e658d147cc8bd8397a1cb88183&claim_uid=DR60NequsjP54Lc" alt="Featured｜HelloGitHub" style="width: 250px; height: 54px;" width="250" height="54" /></a>
