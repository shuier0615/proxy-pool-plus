# CLAUDE.md

本文件为 AI 助手（Claude Code 等）在本仓库工作时提供指导。

## 技术栈

Python 3.12+（开发环境 3.13）、asyncio + aiohttp（抓取与校验）、Flask 3（API）、Redis 5+（存储）、
APScheduler 3.10（调度）、click 8（CLI）、lxml（HTML 解析）。依赖见 `requirements.txt`。

## 常用命令

```bash
pip install -r requirements.txt
pip install -r requirements-test.txt

python proxyPool.py server          # API 服务
python proxyPool.py schedule        # 调度器
python proxyPool.py fetch           # 一次抓取（-s 指定源，--max-new 限制校验量）
python proxyPool.py check           # 一次巡检
python proxyPool.py fetcher         # 列出启用的代理源
python proxyPool.py stats -d        # 池状态
python proxyPool.py reindex         # 升级后重建索引

pytest                              # 全部非集成测试
pytest --cov=. --cov-report=term-missing
REDIS_URL=redis://127.0.0.1:6379/0 pytest -m integration

docker compose up -d --build        # 构建并启动 API + Redis（容器内 gunicorn，非 root）
docker compose logs -f proxy_pool
docker compose down                 # 保留 redis_data 卷
```

## 架构

```
proxyPool.py        CLI 入口（click），子命令拼装 service / scheduler / api
setting.py          默认配置（仅缺省值）
config.py           Settings 冻结 dataclass，环境变量覆盖 + lru_cache
models.py           Proxy（__slots__）：状态字段、序列化、mark_success/failure
service.py          ProxyPoolService：fetch / check / stats / seed，唯一业务编排层
scheduler.py        APScheduler AsyncIOScheduler，协程任务
logging_setup.py    统一日志（proxypool 命名空间，RotatingFileHandler）
api/app.py          Flask 应用工厂 create_app(service)
fetcher/
  base.py           BaseFetcher（抽象 async fetch）、TextListFetcher
  registry.py       pkgutil 自动发现 sources/ 下的子类
  sources/          cn_sites.py / raw_lists.py / api_sources.py / socks_lists.py
checker/
  validator.py      Validator：HEAD→GET 探测，TaskGroup 并发
  region.py         地域补全：ip-api 批量查询 → 中文国家名
  anonymity.py      匿名度判定：回显站看目标站收到的头
storage/redis_store.py  RedisStore：hash + set 索引 + 巡检 zset + 源可用率统计
utils/
  net.py            aiohttp 会话、超时重试、编码兜底
  proxy_parse.py    正则解析 + 公网地址/端口校验
  sampling.py       源可用率平滑 + 加权无放回抽样
  runtime.py        事件循环入口（忽略连接重置噪音）
```

### 数据流

1. `service.fetch()` 通过 `registry.discover_sources()` 拿到启用的源，信号量控制并发，
   每个源用 `asyncio.timeout(max(SOURCE_TIMEOUT, cls.timeout))` 包裹（源可声明更长超时）。
2. 结果按 `proxy -> {source}` 聚合去重，`parse_proxy` 再过滤脏数据，
   用 `store.exists_many()` 批量排除池内已有代理。
3. 配额分配：先按协议拆分（SOCKS5 受 `SOCKS_MAX_NEW_PROXIES` 单独限制），
   再按**源历史可用率**加权抽样（`utils/sampling.py`），最后合并。
   加权依据来自 `{TABLE}:src:checked` / `{TABLE}:src:valid`，每轮结束后累加。
4. `Validator.validate_many()` 并发探测 `HTTP_URL` / `HTTPS_URL`，通过的代理 `put_many` 入库。
5. `service.check()` 从 zset 巡检队列取最久未检的 `CHECK_MAX_PER_ROUND` 条 → 并发复检
   → 失败次数超过 `MAX_FAIL_COUNT` 的删除，其余回写（`put_many` 会把时间戳刷到队尾）。
   单轮耗时只与这个上限有关，与池大小无关；`limit=0` 才做全池复检。

> `RedisStore.put_many` 的索引规则：只有 `last_status=True` 或 `check_count==0`
> （未校验，供 `import` 导入）的代理会进 `:all` / `:https` 索引；失败待复活的只写
> hash 与巡检队列。因此 `/get`、`/pop` 不会拿到刚失败的代理，而 `all()` 仍能看到
> 它们（全池复检需要）。`count()` 的 `available` 即索引集合大小。

### Redis 结构

| key | 类型 | 用途 |
| --- | --- | --- |
| `{TABLE_NAME}` | hash | HTTP 代理：`proxy -> Proxy JSON` |
| `{TABLE_NAME}:all` | set | 全部 HTTP 代理，供 `SRANDMEMBER` 随机取用 |
| `{TABLE_NAME}:https` | set | 支持 HTTPS 的 HTTP 代理 |
| `{TABLE_NAME}:check` | zset | HTTP 巡检队列：`proxy -> 上次校验时间戳` |
| `{TABLE_NAME}:s5` | hash | SOCKS5 代理：`proxy -> Proxy JSON` |
| `{TABLE_NAME}:s5:all` | set | 全部 SOCKS5 代理 |
| `{TABLE_NAME}:s5:https` | set | 支持 HTTPS 的 SOCKS5 代理 |
| `{TABLE_NAME}:s5:check` | zset | SOCKS5 巡检队列 |
| `{TABLE_NAME}:src:checked` | hash | 源历史：`source -> 已校验条数` |
| `{TABLE_NAME}:src:valid` | hash | 源历史：`source -> 可用条数` |

索引缺失时（2.x 老数据）`RedisStore._ensure_index()` 会自动 `reindex()`；
巡检队列缺失时 `check_batch()` 会自动补建（分数 0，优先排队）。

## 关键约定

- **代理源**：继承 `TextListFetcher`（声明 `urls`）或 `BaseFetcher`（实现 `async fetch(session)`），
  `fetch` 返回 `list[str]`；不要自己吞掉网络异常，服务层会统一记录到 `report.errors`。
- **解析**：一律使用 `utils.proxy_parse.parse_proxies` / `normalize`，不要另写正则；
  HTML 页面用 `self.parse_html()` 后 xpath，参考 `cn_sites.py`。
- **配置**：`Settings` 是 frozen dataclass，需要变体时用 `dataclasses.replace()`，不要就地修改；
  测试中改环境变量后必须 `get_settings.cache_clear()`。
- **存储**：只通过 `RedisStore` 访问 Redis，新增查询优先用 set/zset 索引，避免全量扫描。
- **测试**：单元测试用 `fakeredis`（`store` fixture）与 `monkeypatch` 替换网络方法，禁止真实联网；
  集成测试必须标记 `@pytest.mark.integration`。

## 扩展代理源

在 `fetcher/sources/` 新建模块（或在现有分类模块中追加类）：

```python
from fetcher.base import TextListFetcher


class MyFetcher(TextListFetcher):
    name = "myproxy"  # 唯一标识，同时作为来源标记
    url = "https://example.com"
    urls = ("https://example.com/proxies.txt",)
    # enabled = False         # 临时关闭
```

- 自动发现，无需注册；
- 用 `PROXY_FETCHER_EXCLUDE=myproxy`（或类名）临时禁用；
- `python proxyPool.py fetcher` 可验证是否被加载。

新增后建议实测：`python proxyPool.py fetch -s myproxy --max-new 50`。
