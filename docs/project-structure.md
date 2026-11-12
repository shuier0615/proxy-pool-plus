# 项目结构

```text
proxy_pool/
├── proxyPool.py            CLI 入口（click）：server/schedule/fetch/check/fetcher/stats/reindex/import/clear
├── setting.py              默认配置
├── config.py               Settings 冻结 dataclass + 环境变量覆盖
├── models.py               Proxy 模型（__slots__，序列化、健康度标记）
├── service.py              ProxyPoolService：抓取、巡检、统计的唯一编排层
├── scheduler.py            APScheduler AsyncIOScheduler
├── logging_setup.py        日志配置（proxypool 命名空间）
│
├── fetcher/
│   ├── base.py             BaseFetcher / TextListFetcher
│   ├── registry.py         pkgutil 自动发现代理源
│   └── sources/
│       ├── cn_sites.py     国内站点（89ip、云代理、小幻、开心、谷德、FreeVPNNode）
│       ├── socks_lists.py    # SOCKS5 清单（6 源）
│   ├── raw_lists.py    国际纯文本清单（18 个）
│       └── api_sources.py  JSON API（Geonode、Roundproxies、SCDN、Proxifly）
│
├── checker/
│   ├── validator.py        Validator：HEAD→GET 探测 + TaskGroup 并发
│   ├── region.py           地域补全：ip-api 批量 → 中文国家名
│   └── anonymity.py        匿名度判定：回显站看目标站收到的头
│
├── storage/
│   └── redis_store.py      RedisStore：hash + all/https 索引 set + check 巡检 zset
│
├── api/
│   └── app.py              Flask 应用工厂 + 路由
│
├── utils/
│   ├── net.py              aiohttp 会话、重试、编码兜底
│   ├── proxy_parse.py      代理字符串解析与公网地址校验
│   ├── sampling.py         源可用率平滑 + 加权无放回抽样
│   └── runtime.py          事件循环入口（异常降噪）
│
├── tests/
│   ├── conftest.py         fakeredis / app / service fixtures
│   ├── unit/               解析、模型、配置、注册表、存储、校验、编排、CLI
│   ├── api/                Flask 全路由测试
│   └── integration/        需真实 Redis（-m integration）
│
├── docs/                   MkDocs 文档
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 分层职责

| 层 | 模块 | 职责 |
| --- | --- | --- |
| 接入层 | `proxyPool.py`、`api/app.py`、`scheduler.py` | 参数解析、协议适配、定时触发，不含业务逻辑 |
| 编排层 | `service.py` | 决定「抓什么、校验多少、如何入库/剔除」 |
| 领域层 | `fetcher/`、`checker/`、`models.py` | 采集与校验的具体实现 |
| 基础设施 | `storage/`、`utils/`、`config.py`、`logging_setup.py` | Redis、HTTP、解析、配置、日志 |

**改代码时的边界**：API 与 CLI 不应直接操作 `RedisStore` 做业务判断，统一走
`ProxyPoolService`；代理源只负责产出 `host:port`，不关心校验与存储。

## 升级说明（2.x → 3.0）

- 包路径变化：`helper/`、`handler/`、`db/` 已删除，对应能力迁移到
  `service.py`、`models.py`、`storage/redis_store.py`。
- SSDB 支持移除，`DB_CONN` 仅接受 `redis://`。
- 首次启动后执行一次 `python proxyPool.py reindex`（或直接调用 API，索引会自动重建）。
