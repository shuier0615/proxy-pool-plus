# 变更日志

## 3.4.0 (2026-11-12)

1. **出仓检测**：`/get` 与 `/pop` 返回前先实时校验，可用才出；失败自动换下一条
   （`GET_VERIFY_ATTEMPTS` 条，共享 `GET_VERIFY_TOTAL` 秒总预算，超时返回 404）。
   - 失败候选按巡检同一套语义处理：超 `MAX_FAIL_COUNT` 删除，否则下架待复活。
   - `?verify=0` 可跳过检测恢复旧行为；全局开关 `GET_VERIFY`（默认开）。
   - 新增 `ProxyPoolService.pick_verified()`；出仓检测通过时回写最新状态并把
     巡检队列排到队尾。
2. **gunicorn 超时余量**：`WEB_TIMEOUT` 默认 30s → 60s。出仓检测让 `/get` 最长
   约 21s，30s 余量不足；同时降低「哑连接」被误判 WORKER TIMEOUT 的概率。

## 3.3.0 (2026-11-04)

1. **补全 `region` / `anonymous` 两个字段**：此前 `/get` 返回的 `region` 恒为空
   （`PROXY_REGION` 默认关，且逐条查 `api.ip.sb` 太慢），`anonymous` 则是从下载源
   到接口一路都没人写，永远是空串。
   - `checker/region.py` 重写：走 `ip-api.com/batch`，一次 100 个 IP、按 IP 去重，
     几百个代理只需 3~5 次请求；批量失败降级逐条查询（`ip-api.com/json` → `ipapi.co`，
     原来的 `api.ip.sb` 已 403、`ipinfo.io` 已 406）。结果统一转成中文国家名
     （`US` → `美国`，表见 `REGION_NAMES`），未收录代码原样返回。
   - 新增 `checker/anonymity.py`：通过代理访问回显站，按目标站实际收到的头判定
     `transparent`（`X-Forwarded-For` 等被转发，或 `origin` 是多跳）/
     `anonymous`（只有 `Via`、`Proxy-Connection`、代理自身 IP）/
     `high_anonymous`（无痕迹）。实测能准确抓出「origin 里带着真实 IP」的透明代理。
   - 补全逻辑集中在 `ProxyPoolService.enrich()`：抓取后对新入池代理补、巡检时每轮
     顺带补 100 条存量，互不阻塞主流程；`PROXY_REGION` / `PROXY_ANONYMITY` 分别开关，
     `META_CONCURRENCY`（32）/ `META_MAX_PER_ROUND`（400）限流。
   - **存储仍存空串，默认值只在接口输出层补**（`未知` / `unknown`）。写死默认值会让
     「没探测过」和「探测失败」无法区分，补全任务也就再也不会重试。
   - 新增 `python proxyPool.py enrich` 一次性补齐存量数据（只回写真正变化的条目，
     避免全量 `put_many` 把巡检队列整体重置到队尾）。
2. `FetchReport` 新增 `enriched` 字段，抓取日志会打印本轮补全的地域 / 匿名度条数。
3. 测试 234 → **264**：新增匿名度判定（15 个）、地域补全（14 个）、默认值兜底（3 个）用例；
   `conftest` 默认关闭元数据补全，单测不再触网。

## 3.2.1 (2026-11-04)

1. **按源历史可用率加权采样**（新增 `utils/sampling.py`）：公开大清单动辄数万条却
   只有 3%~7% 可用，优质小清单能到 40%，而单轮 `MAX_NEW_PROXIES` 是固定额度，
   按条数均匀抽样等于让劣质清单凭体量把优质源的名额稀释掉——实测一轮校验 5000 条
   候选只留 333 条，九成配额浪费在注定失败的候选上。
   - 每轮抓取结束后把「每个源贡献的候选中有多少通过校验」累加到
     `{TABLE}:src:checked` / `{TABLE}:src:valid`；
   - 下一轮按 `rate = (valid + prior×strength) / (checked + strength)` 做无放回加权
     抽样（Efraimidis-Spirakis，`u ** (1/w)` 取最大 k 个），等权时自动退化为均匀随机；
   - Beta 先验（`SOURCE_WEIGHT_PRIOR=0.1`、`SOURCE_WEIGHT_STRENGTH=10`）避免
     「3 条样本全成功」的源被高估，权重下限 0.02 避免差源被彻底饿死；
   - `SOURCE_WEIGHTING=false` 可回到均匀随机，统计仍继续累积；
   - 统计属于跨轮次学习数据，`clear --yes` 不清除，重置用 `clear --yes --stats`；
   - `python proxyPool.py stats -d` 可直接查看各源累计可用率。
2. **`MAX_FAIL_COUNT` 默认值 0 → 1**：计数是连续语义（成功一次 -1），所以 `1` 表示
   连续两次失败才剔除。此前一次失败即删，配合 2 分钟一轮的增量巡检，每个代理只有
   一次机会，实测新池首轮巡检会掉掉近一半，其中不少只是当时超时。
   配套修一个由此暴露的问题：容错会让「刚失败一次」的代理继续留在索引里被 `/get`
   发出去。现在 `put_many` 只把**校验通过**或**从未校验**（`check_count == 0`，
   用于 `import` 手工导入）的代理放进索引，失败待复活者只保留在 hash 中，
   `/count` 因此新增 `available` / `available_total` 字段表示真正可用的数量。
3. `Stats` 接口 `/count?detail=1` 增加 `source_history` 字段（`checked` / `valid` / `rate`）。
4. `count()` 的 8 次 Redis 读合并为一次 pipeline，并修正索引自愈判据：原判据把
   「代理恰好全部处于下架状态」误判为「索引缺失」，会重新放出它们；现在要求巡检
   队列也为空才重建。
5. 测试 196 → **234**，新增加权抽样（19 个）、源统计（5 个）、加权配额效果（5 个）、
   失败容错与可见性（8 个）用例。

## 3.2.0 (2026-11-04)

1. **修复 SOCKS5 校验长尾（巡检慢 4 倍的根因）**：`_validate_socks` 依赖的
   `aiohttp.ClientTimeout(total=verify_timeout+2)` 在 SOCKS 通道上并没有真正生效，
   实测配置 10s 的探测跑到 **60.04s** 才返回。少量这样的长尾会把整批巡检拖住，
   表现为「并发从 50 加到 800，吞吐恒定 2.5 条/秒」。现在 `Validator.validate()`
   外层增加 `asyncio.timeout(hard_timeout = verify_timeout*2+2)` 硬兜底，
   单个代理无论如何都不可能超时失控。
   同时把 SOCKS5 的 `connect` 预算从 `verify_timeout`(8s) 改为 `min(verify_timeout, 4)`——
   原先 connect 就吃满 total 预算，请求阶段只剩 0 秒余量，会误杀慢而可用的代理。
2. **巡检改为增量队列**：新增 zset `{TABLE}:check` / `{TABLE}:s5:check`
   （分数 = 上次校验时间戳）。`check()` 每轮只取最久未检的 `CHECK_MAX_PER_ROUND`
   条（默认 1200），单轮耗时与池大小**解耦**：此前 704 条已需 72.6s，而巡检周期
   只有 120s，池子涨到约 1200 条就会顶穿周期、开始跳轮。10 万条池子现在每轮仍是
   固定的 1200 条，全池刷新约 167 分钟。
   - 跨协议按分数归并取全局最老的 N 条（先从各自 zset 各取 N 条再截断，结果精确）；
   - `put_many` / `delete` / `pop` / `clear` / `reindex` 同步维护队列；
   - 老数据（只有 hash 没有 zset）由 `check_batch()` 自动补建，无需手工迁移；
   - hash 已删但队列残留的成员会被顺手清理；
   - `proxyPool.py check -n 0` 保留全池复检用于排障。
3. **实测**：同一台机器、同一网络，504 条巡检 **17.4s**（旧版 704 条 72.6s），
   单代理耗时 103ms → 34ms；`-n 100` 连续两轮各检 98 / 95 条，验证按最久未检轮转。
4. 测试 180 → **196**，新增巡检队列（9 个）、增量轮转（4 个）、硬超时（2 个）用例。

## 3.1.2 (2026-11-04)

1. **端口范围校验**：`parse_proxy` 只判断 `port.isdigit()`，`1.2.3.4:0` / `1.2.3.4:99999` 能通过校验进入校验队列，白占配额。现与 `normalize()` 对齐为 1~65535。
2. **脏数据被误删**：`fetch()` 里 `[p.strip() for p in proxies if p and parse_proxy(p)]` 先校验后 strip，源返回的 `" 1.2.3.4:80 "`、`"1.2.3.4:80\n"` 会在 `parse_proxy` 里被判非法而丢弃。改为先 strip 再校验，并让 `parse_proxy` 自身也 strip。
3. **SOCKS5 配额被整体截断稀释**：HTTP 与 SOCKS5 候选混在一个列表里 `[:max_new]` 截断，800 条的 `SOCKS_MAX_NEW_PROXIES` 实际只剩按比例分到的部分（实测 4.5 万 HTTP + 5 千 SOCKS5 时只剩 **96 条**）。改为两种协议分别限额后再合并，HTTP 用剩余配额。
4. **巡检误判空池**：`check()` 的 `refetch_if_low` 用 `count()["total"]`（只统计 HTTP），纯 SOCKS5 池每轮巡检都会触发一次全量抓取。改用 `grand_total`。
5. **抓取互斥**：巡检每 2 分钟一次，池子不足时会主动触发抓取，与定时抓取撞车即重复下载清单、双倍消耗校验配额。`ProxyPoolService` 增加 `asyncio.Lock`，撞上执行中的抓取直接返回空报告。
6. **索引自愈的两处缺陷**：`count()` 用 `https == 0` 判断索引缺失，导致「池内恰好没有 HTTPS 代理」时每次请求都做一轮无用往返，改为用 `all` 集合判断；`pop()` 缺 `_ensure_index()`，老数据（只有 hash）只能 `get()` 不能 `pop()`。
7. **多地址源容错**：`TextListFetcher` 某个 `urls` 失败会抛出异常，整源数据丢弃。现逐个容错，保留其他地址已解析到的代理。
8. **地域查询去重**：`lookup_many` 按 IP 合并，同一出口 IP 的多个端口只查一次。
9. 测试 167 → **180**，新增配额分配、脏数据、互斥、索引自愈、多地址容错、IP 去重用例。

## 3.1.1 (2026-11-04)

1. **修正默认 `DB_CONN`**：由 `redis://:pwdstring@127.0.0.1:6379/0` 改为 `redis://127.0.0.1:6379/0`。前者会向无密码实例发送 `AUTH`，报 `Client sent AUTH, but no password is set`，而 README / docker-compose 的默认 Redis 均为无密码，按文档启动必然连不上。
2. **修复源级超时失效**：`BaseFetcher.timeout`（proxifly 45s、部分 JSON 源 30s）此前被外层 `asyncio.timeout(SOURCE_TIMEOUT=15)` 直接截断，形同虚设。现取 `max(SOURCE_TIMEOUT, cls.timeout)`，慢源能用满自己的预算，普通源仍受全局 15s 约束。
3. **修复集成测试**：`tests/integration/test_redis_store.py` 的 `count()` / `reindex()` 断言仍按 3.1.0 之前的返回结构编写，实际跑 `-m integration` 时 2 个用例失败（CI 默认 `addopts = -m 'not integration'`，因此一直未暴露）。已改为字段级断言，并新增 HTTP 与 SOCKS5 同 `host:port` 互不覆盖的用例。
4. **补测试**：新增 `tests/unit/test_region.py`（地域查询此前覆盖率 38%）与源级超时 3 个用例，测试 154 → 164。
5. **工程一致性**：`pyproject.toml` 版本与 `setting.VERSION` 对齐并补上 `aiohttp-socks` 依赖；全仓执行 `ruff format`（19 个文件）。
6. **修正 JSON 源的协议误判**：Geonode / Roundproxies 在同一个列表里混返 `http` / `https`（TLS 连代理，不可用）/ `socks4` / `socks5`，此前全部按 HTTP 入库，六成以上注定校验失败。现 `_JsonPairFetcher` 支持 `allowed_protocols` 白名单，两源只取 `http`；SCDN 表格按协议列过滤。另外 Geonode 的 `filterLastChecked=10` 实测只剩 2 条（且都是 socks5），放宽到 60 分钟后可取满一页（http 190 条）。
7. **Docker 部署加固**：
   - 新增 `.dockerignore`，此前 `COPY . .` 会把本机 `.venv`（92MB）、缓存与日志全部打进镜像，现构建上下文仅数百 KB；
   - 容器内 API 改用 **gunicorn**（此前日志里明确警告 "This is a development server"），`WEB_WORKERS` 可调；
   - 镜像改为非 root 用户 `app`（uid 100）运行；
   - compose 增加 Redis healthcheck 与 `depends_on: condition: service_healthy`，避免 API 先于 Redis 就绪；
   - 修复 `proxy_pool.sh` 停止时退出码为 1、`Shutting down...` 打印两次的问题（trap 未摘除 + `set -e` 被 `wait` 的 143 打断），现约 1s 优雅退出且 `exit 0`；
   - Redis 故障时接口由 500 改为 **503** `{"code":503,"msg":"redis unavailable"}`，便于负载均衡摘流量。
8. **移除 Windows 兼容代码，只面向 Linux / 容器**：
   - `scheduler.py` 不再 `suppress(NotImplementedError)` 包裹 `add_signal_handler`（Windows Proactor 循环不支持信号回调），直接注册 SIGINT/SIGTERM；
   - `requirements.txt` 去掉 `gunicorn` 的 `platform_system != "Windows"` 标记，`proxy_pool.sh` 删除「未装 gunicorn 回退 Flask 开发服务器」分支，改为启动前显式检查并报错；
   - `utils/runtime.py`、`checker/validator.py`、`logging_setup.py` 中面向 Windows 的注释改为通用表述（降噪处理器与 `trust_env=False` 在 Linux 上同样必要，逻辑保留）；
   - 删除 `_config.yml`（原仓库 GitHub Pages Jekyll 主题，项目已改用 MkDocs，无任何引用）；
   - 文档移除 `Windows: .venv\Scripts\activate` 提示。

## 3.1.0 (2026-11-04)

1. **新增 SOCKS5 支持**（独立命名空间，不影响既有 HTTP 数据）：
   - `Proxy.scheme` 区分 `http` / `socks5`，`as_url` 输出对应协议；老数据无该字段时默认 `http`。
   - Redis 侧新增 `{table}:s5` / `{table}:s5:all` / `{table}:s5:https`，与 HTTP 的 `{table}*` 完全隔离，同一 `host:port` 可同时以两种协议存在。
   - 校验器对 SOCKS5 使用 `aiohttp-socks` 的 `ProxyConnector`（每代理一条短生命周期通道）；未安装该依赖时自动跳过 SOCKS5 源并告警。
   - API 新增 `type=socks5`（`/get`、`/pop`、`/all`、`/delete`），`/all` 支持 `type=all`；`/count` 增加 `socks5`、`socks5_https`、`total` 字段，`count` 仍为 HTTP 总数以保持兼容。
   - 新增 6 个 SOCKS5 源（proxyscrape / aliilapro / zevtyardt / monosans / thespeedx / mmpx12），源总数 30 → 36。
   - 新增配置 `SOCKS_MAX_NEW_PROXIES`（默认 800，`0` 关闭），单独限制单轮 SOCKS5 校验量，避免挤占 HTTP 配额。
2. **修正校验目标地址**：`HTTP_URL` 由 `http://httpbin.org` 改为 `http://connect.rom.miui.com/generate_204`。httpbin 在高并发下大面积返回 429，把可用代理误判为失效——SOCKS5 样本实测 6/60 提升到 29/60。
3. **修正 SOCKS5 校验的 `trust_env`**：必须为 `False`，否则 Windows 会套用系统代理导致流量二次代理（实测 0/60 vs 17/60）。
4. `stats` / `reindex` 输出区分 HTTP 与 SOCKS5；`/sources` 增加 `scheme` 字段。
5. 测试 129 → 154，新增 `tests/unit/test_socks.py`（模型/存储隔离/校验分流/抓取路由）与 API SOCKS5 用例。

## 3.0.2 (2026-11-04)

1. **新增 `proxyscrape_v2`**：v2 legacy 接口与 v4 清单仅 673 条重合，独有 622 条，抽样 120 条可用 24 条（**20%**），是外部清单中质量最好的一档。
2. 核对 23 条 GitHub raw 清单：`TheSpeedX/SOCKS-List/http.txt` 与 `PROXY-List/http.txt` 完全一致；`MuRongPIG/Proxy-Master/socks5.txt` 与其 http.txt 重合 99.85%（实为同批代理）；`vakhov/fresh-proxy-list/https.txt` 仅剩 6 条且与其他源重复；`mmpx12` 的 socks5.txt 首行是 `error code: 1015` 之类错误页残留（解析器已能忽略）。
3. 明确暂不支持 SOCKS4/SOCKS5：清单侧约 3.7 万条 socks5，握手实测可用率 11%~33%，但需要 `aiohttp-socks` 与存储层 scheme 改造，留待评估。

## 3.0.1 (2026-11-04)

1. **GitHub 源镜像回退**：`raw.githubusercontent.com` 在国内网络经常超时，此前 18 个 GitHub 清单源会整批失效。现按「ghproxy.net → gh-proxy.com → fastly.jsdelivr → gcore.jsdelivr → 原始地址」顺序自动重试，首个成功即返回，总耗时受 `SOURCE_TIMEOUT` 约束。实测 29 个源全部可用，合计约 21 万条。
2. **新增 `proxifly_cn`**：Proxifly 中国区清单（`countries/CN/data.json`），实测 134 条中 57 条可用，**可用率 42.5%**，远高于全量清单的 5.8%。
3. **重写 `proxifly` 解析**：改为按 `protocol` 字段白名单过滤。需注意其 `protocol=https` 表示"用 TLS 连接代理本身"，与"能转发 https 目标"是两回事，http 客户端无法直接使用，已排除；同源 `anonymity` 标签实测不可靠（标 elite 的 408 条仅 1 条可用），故不参与筛选。
4. 新增 `utils/net.py::github_mirrors` / `fetch_text_mirrors`，`BaseFetcher.get_text`、`get_json` 默认走镜像回退，新写的源无需关心网络问题。
5. 测试增至 128 个，新增镜像回退与 proxifly 协议过滤用例。

## 3.0.0 (2026-11-04)

1. **运行时升级**：最低 Python 3.12，开发环境 3.13；移除 `util/six.py`、`lazyProperty`、`withMetaclass` 等 PY2 兼容层。
2. **异步重构**：`requests` + 多线程改为 `asyncio` + `aiohttp` 连接池；抓取并发 `FETCH_CONCURRENCY`（默认 16），校验并发 `CHECK_CONCURRENCY`（默认 200）。实测 300 条代理 35 秒完成校验。
3. **存储优化**：新增 `{TABLE_NAME}:all` / `{TABLE_NAME}:https` 索引集合，`/get`、`/pop`、`/count` 由 O(N) 降为 O(1)；批量写入改用 pipeline；2.x 老数据自动重建索引。
4. **移除 SSDB 支持**：`ssdbClient.py` 实为 Redis 协议封装，属于重复实现，统一为 `storage/redis_store.py`。
5. **代理源扩充至 28 个**：新增 TheSpeedX、Monosans、Clarketm、Databay、Vakhov、IPLocate、OpenProxyList、Proxy4Parsing、PrxChk、MuRongPIG、Zevtyardt、ALIILAPRO、hideip.me、RoosterKid、ShiftyTR、mmpx12、ProxyScrape（全球/中国区）。
6. **移除失效源**：快代理（JS 反爬）、站大爷（页面结构变更）、稻壳代理（域名不可达）、66代理（API 502）。
7. **CLI 增强**：新增 `fetch`、`check`、`stats`、`reindex`、`import`、`clear` 子命令；`fetch` 支持 `-s` 指定源、`--max-new` 限制单轮校验量。
8. **API 增强**：新增 `/health`、`/sources`；`/get` 支持 `?format=text`，`/count` 支持 `?detail=1`，`/all` 支持 `?limit=`；无代理时返回 404。
9. **配置现代化**：`config.Settings` 冻结 dataclass，全部配置支持环境变量覆盖并带类型转换；新增 `MAX_PROXIES_PER_SOURCE`、`MAX_NEW_PROXIES`、`LOG_LEVEL`。
10. **日志**：统一 `proxypool` 命名空间，改用线程安全的 `RotatingFileHandler`（10MB × 5）。
11. **测试重写**：110 个单元/API 测试（fakeredis + mock，零联网）+ Redis 集成测试。

## Next (unreleased)

1. 新增代理源 **谷德代理**; (2026-05-14)
2. 引入tox自动化测试, **放弃Python 3.7以下版本支持**; (2026-05-14)
3. 新增统一服务管理脚本 ``proxy_pool.sh``, 支持start/stop/restart/status命令; (2026-05-26)
4. 优化CI配置, 避免PR时重复触发测试; (2026-05-26)
5. 迁移文档从 Sphinx/ReadTheDocs 到 MkDocs Material/GitHub Pages; (2026-05-27)
6. **重写测试套件**: 使用pytest重构全部测试, 覆盖unit/api/integration三层; (2026-05-28)
7. **重构代理采集模块**: 将 `proxyFetcher.py` 拆分为独立文件 + `BaseFetcher` 基类插件架构，支持自动扫描和运行时热更新; (2026-05-31)
   - 每个代理源独立文件（`fetcher/sources/`），继承 `BaseFetcher` 基类
   - 自动扫描启用的代理源，无需在配置中列举
   - 新增 `PROXY_FETCHER_EXCLUDE` 黑名单配置
   - 新增 `proxyPool.py fetcher` 命令查看启用的代理源
8. 新增代理源 **Proxifly**; (2026-06-01)
9. 移除失效代理源 **FreeProxyList**; (2026-06-02)
10. 新增代理源 **66代理 (daili66)**; (2026-06-08)
11. 移除失效代理源 **66代理 (ip66)**; (2026-06-08)
12. 新增代理源 **RoundProxies**; (2026-06-09)
13. 移除失效代理源 **免费代理库 (jiangxianli)**; (2026-06-09)



## 2.4.2 (2024-01-18)

1. 代理格式检查支持需认证的代理格式 `username:password@ip:port`; (2023-03-10)
2. 新增代理源 **稻壳代理**; (2023-05-15)
3. 新增代理源 **冰凌代理**; (2023-01-18)

## 2.4.1 (2022-07-17)

1. 新增代理源 **FreeProxyList**; (2022-07-21)
2. 新增代理源 **FateZero**; (2022-08-01)
3. 新增代理属性 `region`; (2022-08-16)

## 2.4.0 (2021-11-17)

1. 移除无效代理源 **神鸡代理**; (2021-11-16)
2. 移除无效代理源 **极速代理**; (2021-11-16)
3. 移除代理源 **西拉代理**; (2021-11-16)
4. 新增代理源 **蝶鸟IP**; (2021-11-16)
5. 新增代理源 **PROXY11**; (2021-11-16)
6. 多线程采集代理; (2021-11-17)

## 2.3.0 (2021-05-27)

1. 修复Dockerfile时区问题; (2021-04-12)
2. 新增Proxy属性 `source`, 标记代理来源; (2021-04-13)
3. 新增Proxy属性 `https`, 标记支持https的代理; (2021-05-27)

## 2.2.0 (2021-04-08)

1. 启动时检查数据库连通性;
2. 新增免费代理源 **米扑代理**;
3. 新增免费代理源 **Pzzqz**;
4. 新增免费代理源 **神鸡代理**;
5. 新增免费代理源 **极速代理**;
6. 新增免费代理源 **小幻代理**;

## 2.1.1 (2021-02-23)

1. Fix Bug [#493](https://github.com/jhao104/proxy_pool/issues/493), 新增时区配置; (2020-08-12)
2. 修复 **66代理** 采集; (2020-11-04)
3. 修复 **全网代理** 采集, 解决HTML端口加密问题; (2020-11-04)
4. 新增 **代理盒子** 免费源; (2020-11-04)
5. 新增 `POOL_SIZE_MIN` 配置项, runProxyCheck时, 剩余代理少于POOL_SIZE_MIN触发抓取; (2021-02-23)

## 2.1.0 (2020-07)

1. 新增免费代理源 **西拉代理**; (2020-03-30)
2. Fix Bug [#356](https://github.com/jhao104/proxy_pool/issues/356) [#401](https://github.com/jhao104/proxy_pool/issues/401)
3. 优化Docker镜像体积; (2020-06-19)
4. 优化配置方式;
5. 优化代码结构;
6. 不再储存raw_proxy, 抓取后直接验证入库;

## 2.0.1 (2019-10)

1. 新增免费代理源 **89免费代理**;
2. 新增免费代理源 **齐云代理**;

## 2.0.0 (2019-08)

1. WebApi集成Gunicorn方式启动, Windows平台暂不支持;
2. 优化Proxy调度程序;
3. 扩展Proxy属性;
4. 新增cli工具, 更加方便启动proxyPool;

## 1.14 (2019-07)

1. 修复 Queue阻塞导致的 `ProxyValidSchedule` 假死bug;
2. 修改代理源 **云代理** 抓取;
3. 修改代理源 **码农代理** 抓取;
4. 修改代理源 **代理66** 抓取, 引入 `PyExecJS` 模块破解加速乐动态Cookies加密;

## 1.13 (2019-02)

1. 使用.py文件替换.ini作为配置文件;
2. 优化代理采集部分;

## 1.12 (2018-04)

1. 优化代理格式检查;
2. 增加代理源;
3. Fix Bug [#122](https://github.com/jhao104/proxy_pool/issues/122) [#126](https://github.com/jhao104/proxy_pool/issues/126)

## 1.11 (2017-08)

1. 使用多线程验证useful_pool;

## 1.10 (2016-11)

1. 第一版;
2. 支持PY2/PY3;
3. 代理池基本功能;