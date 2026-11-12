# 配置

所有配置都有默认值（见 `setting.py`），并可通过**环境变量覆盖**，无需改动源码。
配置由 `config.Settings`（冻结 dataclass）承载，进程内只解析一次。

## 服务

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | API 监听地址 |
| `PORT` | `5010` | API 监听端口 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `TIMEZONE` | `Asia/Shanghai` | 调度时区 |

## 存储

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DB_CONN` | `redis://127.0.0.1:6379/0` | Redis 连接串，仅支持 `redis://`；有密码写 `redis://:pwd@host:6379/0`，无密码不要保留冒号 |
| `TABLE_NAME` | `use_proxy` | hash 名，索引自动衍生为 `{TABLE_NAME}:all` / `{TABLE_NAME}:https` |

## 代理源

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PROXY_FETCHER_EXCLUDE` | 空 | 禁用的源，逗号或分号分隔，可写 `name` 或类名 |

## 校验

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HTTP_URL` | `http://connect.rom.miui.com/generate_204` | HTTP 校验目标 |
| `HTTPS_URL` | `https://www.qq.com` | HTTPS 校验目标 |
| `VERIFY_TIMEOUT` | `8` | 单次探测超时（秒） |
| `MAX_FAIL_COUNT` | `1` | 允许连续失败次数，超过即剔除。计数为连续语义（成功一次 -1），故 `1` = 连续两次失败才删；`0` = 一次失败就删。未达阈值的会**下架**（留在池内等待复检复活，但 `/get` 不再返回） |
| `POOL_SIZE_MIN` | `20` | 池内数量低于该值，巡检前先抓取一轮 |
| `GET_VERIFY` | `true` | **出仓检测**：`/get` 与 `/pop` 返回前先实时校验，可用才出；失败自动换下一条 |
| `GET_VERIFY_ATTEMPTS` | `3` | 单次请求最多尝试多少条候选 |
| `GET_VERIFY_TOTAL` | `20.0` | 出仓检测总预算（秒），所有尝试共享，超时返回 404 |

## 元数据补全

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PROXY_REGION` | `true` | 补全地域（中文国家名）。走批量接口，按 IP 去重，开销很小 |
| `PROXY_ANONYMITY` | `true` | 探测匿名度（`transparent` / `anonymous` / `high_anonymous`）。每个新代理多一次回显请求 |
| `META_CONCURRENCY` | `32` | 匿名度探测并发数 |
| `META_MAX_PER_ROUND` | `400` | 单轮最多补全多少条；`0` 不限制 |
| `ANONYMITY_URL` | 空 | 自定义回显地址；空则用内置候选中（`httpbin.org` → `httpbingo.org`） |

两项查不到时接口输出 `未知` / `unknown`，Redis 里仍存空串以便下一轮重试。
存量数据可执行 `python proxyPool.py enrich` 补齐。

## 性能

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FETCH_CONCURRENCY` | `16` | 代理源抓取并发数 |
| `CHECK_CONCURRENCY` | `200` | 代理校验并发数，建议 100~500 |
| `CHECK_MAX_PER_ROUND` | `1200` | 单轮巡检最多复检多少条（最久未检优先）；`0` 表示全池复检 |
| `SOURCE_TIMEOUT` | `15` | 单个源整体超时（秒）；源自身声明了更大的 `timeout` 时取较大值 |
| `MAX_PROXIES_PER_SOURCE` | `5000` | 单个源单轮最多保留条数 |
| `MAX_NEW_PROXIES` | `5000` | 单轮最多校验的新代理数 |
| `SOCKS_MAX_NEW_PROXIES` | `800` | 单轮最多校验的 SOCKS5 代理数；`0` 关闭 SOCKS5 |

## 源质量加权

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SOURCE_WEIGHTING` | `true` | 按源历史可用率分配校验配额；关闭后回到按条数均匀随机 |
| `SOURCE_WEIGHT_PRIOR` | `0.1` | 冷启动先验可用率，也是无历史源的权重 |
| `SOURCE_WEIGHT_STRENGTH` | `10` | 先验的虚拟观测次数，越大权重越难被少量样本拉动 |

每轮抓取结束后，会把「每个源贡献的候选中有多少通过校验」累加到 Redis 的
`{TABLE}:src:checked` / `{TABLE}:src:valid` 两个 hash，下一轮按

```
rate = (valid + prior × strength) / (checked + strength)
```

作为权重做无放回加权抽样（Efraimidis-Spirakis，`u ** (1/w)` 取最大 k 个）。
权重有 `0.02` 下限，避免表现差的源被彻底饿死。

理由：公开大清单动辄数万条却只有 3%~7% 可用，优质小清单能到 40%，均匀抽样会让
前者凭体量把后者的名额稀释掉。查看当前权重：

```bash
python proxyPool.py stats -d
```

统计是跨轮次的学习数据，`clear --yes` 不会清除它；需要重置时用
`python proxyPool.py clear --yes --stats`。

## 调度

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FETCH_INTERVAL_MINUTES` | `5` | 抓取间隔（分钟） |
| `CHECK_INTERVAL_MINUTES` | `2` | 巡检间隔（分钟） |

> 巡检是**增量**的：每轮从 Redis 的 `{TABLE}:check` / `{TABLE}:s5:check`（zset，
> 分数为上次校验时间戳）里取最久未检的 `CHECK_MAX_PER_ROUND` 条，因此单轮耗时
> 只与这个值有关，与池子大小无关。想让全池刷新更快就调大它或调小巡检间隔，
> 二者共同决定「全池刷新一轮」的时间：`池大小 / 每轮条数 × 巡检间隔`。

## 示例

```bash
export DB_CONN="redis://:secret@10.0.0.5:6379/1"
export CHECK_CONCURRENCY=300
export PROXY_FETCHER_EXCLUDE=proxyscrape_cn,kxdaili
export HTTP_URL="http://www.baidu.com"
export MAX_FAIL_COUNT=2
export SOURCE_WEIGHT_PRIOR=0.15
```

> 调大 `CHECK_CONCURRENCY` 前请确认系统文件句柄上限（Linux `ulimit -n`，建议 ≥ 8192）。
> 过高的并发会让免费代理服务端限流，反而降低可用率。

## SOCKS5

`SOCKS_MAX_NEW_PROXIES=0` 可完全关闭 SOCKS5 抓取；SOCKS5 需要 `aiohttp-socks`，
未安装时该协议会被自动跳过并在日志中提示。
