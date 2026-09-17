# 股票新闻免费搜索引擎可行性研究与架构方案

## 1. 背景与目标

目前本系统在 `src/search_service.py` 和 `.env.example` 中主要依赖商业搜索 API（如 `TAVILY_API_KEYS`、`BOCHA_API_KEYS` 等）获取股票实时新闻与多维情报。
由于本项目为个人/开源项目，商业 API 存在以下痛点：
1. **费用与配额瓶颈**：Tavily 免费版每月仅 1000 次请求，SerpAPI 仅 100 次；每天复盘分析多只股票（包含个股新闻、题材热点、业绩预期、风险排查等多个搜索维度）极易迅速耗尽配额。
2. **门槛与维护成本**：用户部署必须逐一注册三方平台、绑定邮箱、获取 API Key，且需要关注配额消耗情况。

**目标**：调研分析免 API Key、免注册或零成本的替代搜索引擎（包括用户提出的 Agent-Reach、Google、Bing 等），评估其在时效性、防爬风控、字段契约、网络连通性等维度的可行性，整理出完整可行性研究报告与工程实装方案。

---

## 2. 系统现有架构与契约深度分析

在引入任何新搜索引擎之前，必须严格对齐 `src/search_service.py` 内部的核心契约：

### 2.1 数据结构契约

系统内部所有搜索结果必须规范化封装为 `SearchResult` 和 `SearchResponse`：

```python
@dataclass
class SearchResult:
    title: str               # 新闻标题
    snippet: str             # 新闻摘要/正文片段
    url: str                 # 新闻原链接
    source: str              # 来源媒体/站点
    published_date: Optional[str] = None  # 发布日期（核心字段！）
    relevance_score: Optional[int] = None
    ...

@dataclass
class SearchResponse:
    query: str
    results: List[SearchResult]
    provider: str
    success: bool = True
    error_message: Optional[str] = None
    search_time: float = 0.0
```

### 2.2 时效性与发布时间硬过滤（核心生命线）

在 `SearchService._filter_news_response` 中，系统对新闻有着严格的**硬过滤机制**（根据 `NEWS_MAX_AGE_DAYS`，默认 3 天或 7 天）：
- **必须具备可解析的发布时间**：如果搜索引擎返回的结果缺少 `published_date`，或者时间格式无法被 `_normalize_news_publish_date` 识别，且当前维度未开启 `keep_unknown`，则该结果会被记为 `dropped_unknown` 并**全数丢弃**（历史 issue #782 曾出现 Tavily 虽有返回但因字段映射缺失导致新闻全灭的情况）。
- 系统内置了丰富的日期解析器（支持 ISO 格式、时间戳、中文日期、相对时间如“3天前”、以及 RFC 822 邮件/RSS 时间格式）。
- **结论**：候选引擎**必须能够稳定返回新闻的真实发布时间**，否则无法通过系统的时效性校验。

### 2.3 业务调用场景矩阵

| 场景方法 | 查询语句特征 | 时效性要求 | 说明 |
| --- | --- | --- | --- |
| `search_stock_news` | `{股票名称} {股票代码} 股票 最新消息` / 美股英文名 | 严格（`search_days`，通常 3 天） | 个股即时新闻，需计算直接相关度 |
| `search_topic_news` | `"{题材}" A股 最新消息 催化` | 严格（通常 3 天） | 行业热点、板块题材催化 |
| `search_comprehensive_intel` | 5 个子维度（最新消息、机构分析、风险排查、业绩预期、行业分析） | 最新消息/风险排查严格；机构/业绩/行业较宽（30天） | 多维情报矩阵 |
| `search_stock_price_fallback` | 行情备用搜索 | 宽容 | 数据源降级 |

### 2.4 多引擎轮询与降级机制（Fail-Open）

`SearchService` 内部维护了按优先级排序的 `self._providers: List[BaseSearchProvider]`：
- 依次调用可用引擎，若某引擎失败或过滤后无可用新闻（`NoUsableNews`），会自动尝试下一个引擎。
- 绝不能因单一引擎异常抛出未捕获错误而中断整个股票分析主链路。

---

## 3. 候选免费搜索方案可行性调研与实测

针对用户提及的 **Agent-Reach**、**Google**、**Bing**，以及社区常用的 **DuckDuckGo**、**SearXNG**、**国内财经开放接口** 等进行了全面对比和实际请求测试。

### 3.1 方案 A：Agent-Reach 深入分析（用户提及）

*   **项目背景**：GitHub 热门项目 `Panniantong/Agent-Reach`，标榜“给 AI Agent 看懂整个互联网的能力，零 API 费用阅读与搜索 Twitter、Reddit、B站、小红书等”。
*   **架构实质调研**：
    1. Agent-Reach 的定位是针对命令行交互式 Coding Agent（如 Claude Code、Cursor、OpenClaw）的**能力胶水层（Capability Layer）**，而不是一个独立的搜索服务。
    2. 它的实现方式是依赖 Agent 宿主执行命令行工具，例如借用桌面 Chrome 浏览器现成的会话 Cookie（OpenCLI）爬取社媒，用 `yt-dlp` 爬 YouTube 字幕，用 `feedparser` 读 RSS。
    3. **对于 Web 搜索**：Agent-Reach 自身**没有自研搜索**，其全网搜索底层通过 `mcporter` 调用 **Exa** 或 **Jina Search**。
    4. **免费性验证**：我们对 Jina Search API (`https://s.jina.ai/{query}`) 进行了实际网络请求测试：
       ```text
       HTTP Error 401: Unauthorized
       ```
       证实 Jina 当前已要求 API Key 鉴权，且 Exa 同样是商业 API。
*   **可行性结论**：**不可行（Infeasible）**。
    - **重型外部依赖**：需要 Node.js、mcporter、各类独立 CLI 工具链，与 DSA 轻量 Python 纯后台服务理念冲突。
    - **交互与登录态**：很多平台强依赖本地桌面 Chrome 登录态，无法在 GitHub Actions、无头 Docker 或服务器守护进程中运行。
    - **缺乏通用免费免 Key 搜索**：其搜索能力本身仍需第三方 API Key。

---

### 3.2 方案 B：Bing News RSS（微软必应新闻公开订阅）

*   **技术原理**：
    利用必应新闻的公开 RSS 接口：
    `https://www.bing.com/news/search?q={query}&format=rss`
    （可通过 `qft=interval="7"` 约束时间，或由系统本地做精确时间过滤）。
*   **实测数据验证**：
    实测针对 `600519 贵州茅台` 发送请求：
    - **响应耗时**：约 0.8s ~ 1.5s。
    - **返回字段**：完整的 XML RSS 格式：
      - `<title>`：完整新闻标题。
      - `<pubDate>`：格式如 `Thu, 17 Sep 2026 02:24:00 GMT`（标准 RFC 822）。
      - `<description>`：包含该条新闻的要点摘要（约 100~200 字，含最新进展）。
      - `<link>`：新闻跳转链接。
    - **时效契约对齐**：`src/search_service.py` 内部已导入 `parsedate_to_datetime`，能直接将 `Thu, 17 Sep 2026 02:24:00 GMT` 100% 精确解析为本地 `datetime.date`，完美通过系统的 `_filter_news_response` 校验！
*   **网络连通性与风控**：
    - 国内网络环境下，`bing.com` / `cn.bing.com` **免翻墙可直接连通**。
    - RSS 规范接口对抓取极其友好，比常规网页版 HTML 抓取的反爬风控低几个数量级，几乎不弹验证码。
*   **可行性结论**：**极高（Highly Recommended，推荐作为第一顺位免费引擎）**。

---

### 3.3 方案 C：Google News RSS（谷歌新闻公开订阅）

*   **技术原理**：
    利用谷歌新闻官方的公开 RSS 检索端点：
    `https://news.google.com/rss/search?q={query}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans`
    （美股可用 `hl=en-US&gl=US&ceid=US:en`）。
*   **实测数据验证**：
    实测针对 `600519 贵州茅台 when:3d` 发送请求：
    - **检索响应**：极速返回 7 条权威财经新闻（同花顺、搜狐财经、财中社等）。
    - **时间过滤原生支持**：直接在 query 后面拼接 `when:1d` / `when:3d` / `when:7d`，Google 服务端即可先做一次高精度时间过滤。
    - **发布时间**：包含精确的 `<pubDate>`（RFC 822），完全兼容 DSA 的日期硬过滤。
    - **缺点**：Google News 返回的 `<description>` 为简短 HTML 超链接，正文 snippet 稍弱于 Bing News；国内网络受 GFW 阻断。
*   **网络连通性与风控**：
    - 国内直接访问受阻，需走本地代理（`HTTP_PROXY`）。但在海外 VPS、Docker 容器或 GitHub Actions CI/CD 环境中完全畅通。
    - RSS 端点免 API Key、免注册、零费用。
*   **可行性结论**：**高（Recommended，推荐作为第二顺位/并发兜底引擎）**。

---

### 3.4 方案 D：DuckDuckGo（`duckduckgo_search` / `ddgs`）

*   **技术原理**：使用开源库逆向调用 DuckDuckGo 搜索/新闻接口。
*   **实测数据验证**：
    我们在当前环境直接运行 `DDGS().news(keywords='600519 贵州茅台', max_results=3)`：
    ```text
    duckduckgo_search.exceptions.RatelimitException:
    https://duckduckgo.com/news.js... 403 Ratelimit
    ```
    **首个请求即被 DDG 风控拦截，返回 403 Ratelimit**！
*   **缺陷**：
    - DDG 近年来加大了对自动化请求和数据中心 IP 的封禁力度（VQD 动态校验、IP 限频）。
    - 经常需要跟进更新 pip 包，稳定性极不稳定，不适合作为自动化分析的骨干引擎。
*   **可行性结论**：**低（不推荐作为主力引擎，仅可作为低优先级边缘补充）**。

---

### 3.5 方案 E：SearXNG（自建容器 vs 公共实例池）

*   **技术原理**：项目当前已实现 `SearXNGSearchProvider`。
    - **自建私有实例**：本地或局域网运行 `docker run -d -p 8080:8080 searxng/searxng`，在 `settings.yml` 中开启 `format: json`，并在 `.env` 中配置 `SEARXNG_BASE_URLS=http://localhost:8080`。
    - **公共实例模式**：从 `searx.space` 拉取公共节点。
*   **现状剖析**：
    - 自建实例模式：体验极佳，真正完全免费无限制，支持聚合 Google、Bing、Baidu 等底层引擎。
    - 公共实例模式：公网上绝大多数公共节点为防抓取都关闭了 JSON 格式输出或被 Cloudflare 拦截，故障率较高。
*   **可行性结论**：**已实现，继续保留并完善文档说明，作为高级用户私有化部署的首选**。

---

## 4. 综合对比矩阵

| 维度 | Tavily (现状) | Bing News RSS | Google News RSS | DuckDuckGo | SearXNG (自建) | Agent-Reach |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **API 费用** | 免费1000次/月，超量收费 | **100% 完全免费** | **100% 完全免费** | 免费 | 免费（需自备服务器） | 依赖第三方付费 API |
| **API Key 门槛** | 必须注册申请 Key | **无需注册，免 Key** | **无需注册，免 Key** | 免 Key | 免 Key | 需配置环境与多平台账号 |
| **新闻发布时间** | 支持（需显式映射） | **RFC 822 精确时间** | **RFC 822 精确时间** | 包含时间 | 支持 `publishedDate` | 视底层渠道而定 |
| **通过 DSA 严苛时效过滤** | 是 | **是（完美匹配）** | **是（完美匹配）** | 较差 | 是 | 否 |
| **国内直连（免代理）** | 是（部分有波动） | **是（Bing 国内畅通）** | 否（需代理） | 否 | 是（自建在境内时） | 视各社媒而定 |
| **反爬抗风控能力** | 商业保障 | **高（标准 RSS 协议）** | **高（标准 RSS 协议）** | **极低（实测即 403）** | 取决于底层 | 低（需模拟登录态） |
| **适合 DSA 架构** | 适配良好 | **适配极佳** | **适配极佳** | 差 | 适配良好 | **不适用（体系不兼容）** |

---

## 5. 推荐实装设计方案

### 5.1 架构设计

在 `src/search_service.py` 中新增两个轻量、零依赖（仅使用现有 `requests` 和标准库 `xml.etree.ElementTree`）的 SearchProvider：
1. `BingNewsSearchProvider(BaseSearchProvider)`
2. `GoogleNewsSearchProvider(BaseSearchProvider)`

两者继承 `BaseSearchProvider`，遵循现有的 `_do_search`、重试策略与异常处理机制。

### 5.2 优先级编排策略

保持现有系统的平滑升级与向后兼容：
```text
1. 商业/高优配置（若用户填了 Key 则优先享受对应质量）：
   Bocha (如有) -> Tavily (如有) -> Brave (如有) -> SerpAPI (如有) -> MiniMax (如有) -> Anspire (如有)
2. 免 Key 免费新闻引擎（只要未配置商业 Key，自动无缝 fallback 兜底）：
   -> Bing News RSS（国内直连，高时效）
   -> Google News RSS（海外/代理直连，高权威度）
3. 私有化无限制引擎（如有自建）：
   -> SearXNG（自建实例优先）
```

**对个人用户的收益**：
- 用户在 `.env` 中**哪怕一个搜索 Key 都不填**，系统也不会打印“未配置任何搜索能力，新闻搜索功能将不可用”的警告。
- 系统会自动无缝使用 Bing News 和 Google News 获取个股新闻与多维情报，真正做到**零元开箱即用**！
- 如果未来某天用户有了 Tavily Key，直接填入即可无缝升阶为优先走 Tavily。

### 5.3 环境变量与配置项对齐

在 `.env.example` 和 `src/config.py` 中增加开关（保持极简，默认开启）：
```env
# ===================================
# 免费新闻搜索引擎配置（无需 API Key）
# ===================================
# 启用 Bing 新闻搜索（免 Key，国内直连畅通，默认 true）
BING_NEWS_SEARCH_ENABLED=true

# 启用 Google 新闻搜索（免 Key，海外/带代理环境畅通，默认 true）
GOOGLE_NEWS_SEARCH_ENABLED=true
```

---

## 6. 验证与回归测试规划

1. **单测覆盖**：
   - 编写 `tests/test_search_bing_news_provider.py`，使用 Mock 验证 RSS XML 解析、RFC 822 时间转换、字段映射与网络异常容错。
   - 编写 `tests/test_search_google_news_provider.py`，验证 `when:3d` 参数拼接与解析。
2. **时效过滤回归测试**：
   - 验证 `BingNewsSearchProvider` 和 `GoogleNewsSearchProvider` 返回的数据送入 `_filter_news_response` 不会被作为 `dropped_unknown` 丢弃。
3. **真实环境 Smoke 测试**：
   - 在未配置 `TAVILY_API_KEYS` 的情况下，运行一次真实个股分析检索（如 600519、00700、AAPL），验证新闻是否成功检出并注入上下文。
