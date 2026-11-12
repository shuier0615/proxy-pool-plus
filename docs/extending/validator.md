# 校验机制

代理只有能真正转发请求才会入池。校验逻辑在 `checker/validator.py`。

## 流程

```text
Proxy ──▶ 格式校验(parse_proxy) ──▶ HTTP 探测(HTTP_URL)
                                      │
                                      ├─ 失败 ──▶ fail_count += 1，不入池 / 计入剔除
                                      └─ 成功 ──▶ HTTPS 探测(HTTPS_URL)
                                                    ├─ 成功 ──▶ https=True
                                                    └─ 失败 ──▶ https=False（仍可用）
```

探测细节：

1. 先发 `HEAD`，状态码 `< 400` 视为可用；
2. 若代理连不上（`ClientConnectorError` / 超时 / 握手失败），**直接判失败**，
   不再补发 GET —— 避免每个死代理消耗双倍超时；
3. 若代理可用但目标站拒绝 `HEAD`（405/403 等），降级为流式 `GET` 只读 1 字节。

## 并发模型

`Validator.validate_many()` 用 `asyncio.TaskGroup` + `Semaphore(CHECK_CONCURRENCY)`
并发校验，共享一个 `aiohttp.ClientSession`（连接池上限即并发上限）。

实测：300 条代理、200 并发、`VERIFY_TIMEOUT=8`，约 35 秒完成。

## 判定与剔除

| 场景 | 处理 |
| --- | --- |
| 新代理校验失败 | 不入库 |
| 池内代理校验成功 | 回写状态，`fail_count` 自减（最低 0） |
| 池内代理校验失败 | `fail_count += 1`；超过 `MAX_FAIL_COUNT` 则删除，否则保留待下次复检 |

`MAX_FAIL_COUNT=0` 表示一次失败即剔除（默认，适合免费代理波动大的场景）。

## 自定义校验目标

```bash
export HTTP_URL="http://www.baidu.com"
export HTTPS_URL="https://www.baidu.com"
export VERIFY_TIMEOUT=5
```

目标站要求：无鉴权、响应快、HEAD 可用（不支持时会自动降级 GET）。

## 地域与匿名度

两项都在 `service.enrich()` 里补全，与校验解耦——校验只回答「能不能用」，
能不能暴露真实 IP 是另一个维度，混在一起会让 `validate()` 的职责变模糊。

**地域**（`PROXY_REGION=true`，默认开）：走 `ip-api.com/batch`，一次最多 100 个 IP，
按 IP 去重后批量查询，几百个代理只需 3~5 次请求；批量失败时降级为逐条查询
（`ip-api.com/json` → `ipapi.co`）。结果转成中文国家名写入 `Proxy.region`。

> 早期的 `api.ip.sb` 已返回 403、`ipinfo.io` 返回 406，不要再改回去。

**匿名度**（`PROXY_ANONYMITY=true`，默认开）：通过代理访问回显站
（`http://httpbin.org/get`，兜底 `https://httpbingo.org/get`），看目标站实际收到的头：

- 出现 `X-Forwarded-For` / `Forwarded` / `X-Real-IP` → `transparent`
- `origin` 是多跳（`"客户端IP,代理IP"`）→ `transparent`
- 只有 `Via` / `Proxy-Connection` / 代理自身 IP → `anonymous`
- 什么都没有 → `high_anonymous`

单个代理多一次请求，因此由 `META_CONCURRENCY`（默认 32）限流、
`META_MAX_PER_ROUND`（默认 400）限制单轮条数。探测失败留空，下一轮重试。

## 扩展

需要更强的判定（如要求返回特定内容）时，在 `Validator` 中增加探测方法，
并在 `validate()` 里组合。例如：

```python
async def _probe_body(self, session, url, proxy_url, expect: str) -> bool:
    async with session.get(url, proxy=proxy_url, ssl=False) as response:
        return expect in await response.text()
```

修改后请补充 `tests/unit/test_validator.py` 的用例（用 `monkeypatch` 替换 `_probe`，不要真实联网）。
