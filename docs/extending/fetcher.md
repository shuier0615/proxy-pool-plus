# 扩展代理源

代理源放在 `fetcher/sources/` 下，启动时由 `fetcher/registry.py` 通过 `pkgutil`
自动发现，**无需注册、无需改配置**。

## 最简形式：纯文本清单

如果目标是一个返回 `ip:port` 列表的 URL（每行一条，JSON/HTML 之外的任意分隔）：

```python
from fetcher.base import TextListFetcher


class MyFetcher(TextListFetcher):
    name = "myproxy"  # 唯一标识，同时作为来源标记
    url = "https://example.com"  # 源站首页，仅用于展示
    urls = (
        "https://example.com/proxies.txt",
        "https://example.com/proxies2.txt",
    )
```

`TextListFetcher` 会并发/串行请求 `urls`，用统一的 `parse_proxies` 提取并去重。

## GitHub 源镜像回退

大量清单托管在 GitHub，而 `raw.githubusercontent.com` 在国内网络经常超时。
`BaseFetcher.get_text()` / `get_json()` 已内置镜像回退：凡是
`https://raw.githubusercontent.com/<owner>/<repo>/<branch>/<path>` 形式的地址，
会依次尝试 ghproxy.net → gh-proxy.com → fastly.jsdelivr → gcore.jsdelivr → 原始地址，
首个成功即返回，总耗时受 `SOURCE_TIMEOUT` 约束。

因此写源时**直接用 GitHub raw 地址即可**，不要手动拼镜像：

```python
urls = ("https://raw.githubusercontent.com/owner/repo/main/http.txt",)
```

若需自行控制候选顺序，可调用 `utils.net.github_mirrors(url)` 拿到候选列表。
注意 jsDelivr 走 CDN 缓存（最长约 12 小时），只作兜底，实时性要求高的源不宜只依赖它。

## 自定义解析：JSON / HTML

```python
import aiohttp
from fetcher.base import BaseFetcher
from utils.proxy_parse import normalize


class MyJsonFetcher(BaseFetcher):
    name = "myjson"
    url = "https://example.com/free-proxy"
    api = "https://example.com/api/proxies?limit=100"

    async def fetch(self, session: aiohttp.ClientSession) -> list[str]:
        payload = await self.get_json(session, self.api)
        if not isinstance(payload, dict):
            return []
        result = []
        for item in payload.get("data", []):
            if proxy := normalize(str(item.get("ip", "")), item.get("port", 0)):
                result.append(proxy)
        return result
```

HTML 页面用 `self.parse_html(text)` 得到 lxml 树：

```python
class MyHtmlFetcher(BaseFetcher):
    name = "myhtml"
    url = "https://example.com/free"

    async def fetch(self, session):
        tree = self.parse_html(await self.get_text(session, self.url))
        if tree is None:
            return []
        rows = []
        for tr in tree.xpath("//table//tr"):
            cells = ["".join(td.xpath(".//text()")).strip() for td in tr.xpath("./td")]
            if len(cells) >= 2 and (p := normalize(cells[0], cells[1])):
                rows.append(p)
        return rows
```

## 约定

| 属性 | 说明 |
| --- | --- |
| `name` | 必填，全局唯一，入库后作为 `source` 字段 |
| `url` | 源站首页，`/sources` 与 CLI 展示用 |
| `enabled` | 设为 `False` 可直接下线该源 |
| `timeout` | 单次请求超时，默认 15 秒 |
| `protocols` | 声明支持的协议，默认 `("http",)` |

- `fetch()` 是 `async` 方法，返回 `list[str]`，元素为 `host:port` 或 `user:pass@host:port`。
- 不要在源里 `try/except` 吞掉所有异常：服务层会按源捕获，写入 `report.errors` 并继续跑其他源。
- 解析一律用 `utils.proxy_parse`，它会过滤内网/保留地址和非法端口。

## 调试

```console
# 确认被加载
python proxyPool.py fetcher

# 只跑这个源，最多校验 50 条
python proxyPool.py fetch -s myproxy --max-new 50
```

临时禁用（不改代码）：

```bash
export PROXY_FETCHER_EXCLUDE=myproxy
```

## 现成分类

| 文件 | 适用场景 |
| --- | --- |
| `sources/raw_lists.py` | GitHub / API 返回的纯文本清单 |
| `sources/api_sources.py` | 返回 JSON 的接口 |
| `sources/cn_sites.py` | 需要 xpath 或正则的 HTML 站点 |
