# 来源受控复核（2026-10-24）

本轮只做一次受控的公开来源快照复核，不启动生产、不修改启用状态、不新增或猜测 URL。机器可读结果见
[`source-recheck-2026-10-24.json`](source-recheck-2026-10-24.json)；历史
[`source-validation.json`](source-validation.json) 未覆盖。

> 该 JSON 是初测的原始证据（初版报告 schema 1），本轮返修后**没有重新发起实网请求，也未改写该文件**。
> 其中的 26/19/4/3 计数仍是 2026-10-24 初测结果；修复后的脚本写 schema 2，元数据语义已按下方“安全返修”更新。

## 范围与方法

- 来源清单来自 `config/sources.yaml` 中已登记的 26 个来源；不打印、导出或修改 `.env`/凭据；运行时仅通过现有 `security.fetch`/`settings()` 使用项目已配置的 HTTP 安全限额（这会按项目配置隐式加载 `.env`），未直接检查密钥。
- 串行执行：每源一次采集尝试，不自动重试；同一时刻最多一个采集尝试。底层 `security.fetch` 允许跟随经逐跳重新校验的重定向（最多 5 跳），重定向跳转不是重试。
- 同域名采集尝试间隔 1.0s；所有 arXiv feed 共用至少 3.1s 间隔（复核脚本与 `security.fetch` 的 arXiv 限速均保留）。
- 复用现有 collector 与 `app.core.security.fetch`：20s HTTP 阶段超时、5MB 响应上限、逐跳校验跳转、禁环境代理、SSRF 校验不变。
- 只记录检查时间、HTTP 状态或规范错误码、格式、条目数、字段覆盖、最近显式时间、旧 `enabled` 状态；不保存正文或原始响应。
- 运行前后校验配置 SHA-256：`c6316939496482fe123437fb56b7821f96a15e68dafde8a40a3b0555e039ad4f`，未变化。
- 初测命令：`python -m scripts.recheck_sources --output docs/source-recheck-2026-10-24.json`。
- 本次快照时间：2026-10-24T15:13–15:14Z；所有实网结论仅对该时点有效。

## 安全返修（Codex 复核后）

Codex 独立复现出 6 项问题，均已在脚本与测试中修复；未重跑实网，原始 JSON 保留：

1. 输出临时文件改为目标目录内 `tempfile.mkstemp` 随机名、排他创建；只清理本轮自己创建的文件，不再固定使用或删除 `*.tmp` sidecar。
2. 发布报告不再跟随符号链接写文件；预先存在的 sidecar/符号链接不会被触碰。`--force=False` 时用无覆盖的硬链接发布，若运行中另一进程创建了输出，则返回 `OUTPUT_EXISTS_DURING_RUN` 并保留对方数据；只有 `--force=True` 才替换已有报告，且输出路径本身是符号链接时仍拒绝。
3. `domain_interval` 在任何请求前拒绝 `nan`/`inf`/负值/非数值，保留 arXiv 3.1s 保护，未改 `security.fetch`。
4. `parse_config` 在配置阶段拒绝 list/dict 等非法 `source_type`，不会进入 collector 或发请求。
5. 输出/配置的 `PermissionError`/`OSError` 统一转为短 `RECHECK_ERROR` 与 exit 3，不打印原始异常文本；发布失败会清理本轮临时文件，不丢失已有输出。
6. 元数据从“单次 HTTP 请求”改为“每源一次采集尝试、不自动重试；底层允许经校验的重定向”。已有 JSON 的旧字段原样保留，修复后脚本写 `max_collection_attempts_per_source`、`automatic_retries` 等字段。

## 结果总览

26 个已登记来源中：**19 个解析出条目、4 个合法空列表、3 个失败**。原配置中 22 个为启用状态，
其中 18 个解析出条目、4 个 arXiv 为空、没有硬失败；3 个失败项均原本就是禁用状态。原配置未被本轮修改。

脚本初测退出码为 1：按脚本定义，存在空列表或失败且并非全部解析成功时为 1；这不是崩溃，也不代表全部来源不可用。
不能用历史“22 个启用”或本轮“19 个有条目”直接当作当前可用来源数，也不能据此推断转载许可。

| 来源 | 旧 enabled | 结果 | HTTP | 条目 | 最近显式时间（UTC） | 错误码 |
|---|---:|---|---:|---:|---|---|
| NVIDIA Newsroom | true | parsed | 200 | 20 | 2026-10-23T02:00:13 | — |
| NVIDIA Blog | false | failed | — | 0 | — | ConnectTimeout |
| NVIDIA Developer | true | parsed | 200 | 100 | 2026-10-23T17:27:52 | — |
| NASA News | true | parsed | 200 | 10 | 2026-10-24T04:05:00 | — |
| NASA Technology | true | parsed | 200 | 10 | 2026-10-22T04:01:00 | — |
| NASA Aeronautics | true | parsed | 200 | 10 | 2026-10-21T16:31:11 | — |
| NASA Station | true | parsed | 200 | 10 | 2026-10-21T15:05:08 | — |
| NASA Artemis | true | parsed | 200 | 10 | 2026-10-23T22:39:39 | — |
| EIA Today in Energy | true | parsed | 200 | 20 | 2026-10-22T14:00:00 | — |
| EIA Press | true | parsed | 200 | 12 | 2026-10-06T17:00:00 | — |
| EIA Gas Diesel | true | parsed | 200 | 1 | 2026-10-20T14:34:54 | — |
| ECB Press | true | parsed | 200 | 15 | 2026-10-24T06:00:00 | — |
| ECB Blog | true | parsed | 200 | 15 | 2026-10-21T09:00:00 | — |
| ECB Statistics | true | parsed | 200 | 15 | 2026-10-20T08:00:00 | — |
| ECB Publications | true | parsed | 200 | 15 | 2026-10-19T09:00:00 | — |
| ECB Working Papers | true | parsed | 200 | 15 | 2026-10-19T09:00:00 | — |
| ECB Research Bulletin | true | parsed | 200 | 15 | 2026-10-08T06:00:00 | — |
| GDELT AI | false | parsed | 200 | 50 | — | — |
| arXiv cs.AI | true | empty | 200 | 0 | — | — |
| arXiv cs.LG | true | empty | 200 | 0 | — | — |
| arXiv cs.RO | true | empty | 200 | 0 | — | — |
| arXiv q-bio | true | empty | 200 | 0 | — | — |
| 36氪快讯 | false | failed | 200 | 0 | — | ValueError:INVALID_FEED |
| NIH NIDCR Science News | false | failed | 404 | 0 | — | HTTP_404 |
| FDA Press Releases | true | parsed | 200 | 20 | 2026-10-22T16:58:19 | — |
| FDA Drugs | true | parsed | 200 | 20 | 2026-10-23T22:27:41 | — |

“最近显式时间”只取 collector 未标记为推断的时间；它只是 feed 中最新条目的时间，不证明内容已更新、已被本项目采集或允许转载。

## 空列表：arXiv 四个 feed

四个 arXiv feed 均返回 HTTP 200 的合法 RSS（`application/rss+xml`）但 0 条目。arXiv 官方 RSS 说明只说明
feed **每日批量更新**，没有解释本次四个分类为何同时为空；因此空列表原因未知，**不归因于周六、周末或发布节奏**，
也不据此认定这些分类当前无内容。记为 `empty`，不记为解析成功，也不据此改启用状态；需工作日再做一次单次快照确认。
此前 2026-10-16 的快照有数百条，只能说明当时该时点有内容。

## GDELT：本轮未复现失败

本轮对该已登记 URL 只做一次采集尝试，结果：

- 2026-10-24T15:14Z，HTTP 200，`application/json; charset=utf-8`，50 条文章记录，耗时 19.11s。
- 字段覆盖：标题 50/50、原始链接 50/50、`published_at` 50/50、外部 ID 50/50、摘要 0/50。
- 未出现协议错误；没有当前可复现的失败。**不因此启用生产来源**：`config/sources.yaml` 中仍为禁用。

历史失败为 2026-10-16T08:55:40Z，旧验证器只记下异常类型 `RemoteError`，没有 HTTP 状态或规范错误码，
因此**无法准确归因**。该次耗时 12.6s。旧记录不足以区分 HTTP 错误、响应过大或跳转问题，也不能据此认定是网络/第三方限制。

官方依据（本次实际查看）：

- [GDELT DOC 2.0 API 说明](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)：支持 JSON/JSONP；
  ArtList 模式；`MAXRECORDS` 最大 250（本配置用 50）；`TIMESPAN` 最小 15 分钟（本配置用 1d）。
  该页未给出明确数字速率/配额。
- [GDELT Terms of Use](https://gdeltproject.org/about.html#termsofuse)：数据集可用于学术、商业、政府用途并允许再分发，
  但须引用 GDELT 并链接官网；这不改变被索引原出版者的文章版权，也不等于本项目获得全文转载权。

稳定性风险：本次 19.11s 接近 `httpx` 的 **20s HTTP 阶段超时**（单项/读取阶段），而不是 `security.fetch` 的
40s 总 deadline；两者含义不同，下一次仍可能先触发阶段超时。维持禁用，待 Codex 决定是否另行观察。
不为“跑通”而降低超时/大小限制、绕过访问控制或替换为无官方出处的 URL。

### 时间口径：保留保守标记，不改 collector

本轮 GDELT 的 `published_at` 覆盖为 50/50，但 `explicit_time_entries` 为 0、`latest_published_at` 为 null。
这是预期行为：`app/collectors/gdelt.py` 把 GDELT 的 `seendate` 写入 `published_at` 时同时保留
`publication_time_inferred=True`，因为 `seendate` 是 GDELT 收录/观测时间，**不能直接证明原站发布时间**。
因此 `latest_published_at=null` 是保守口径，不是已证实的解析错误。

未来若需要展示 GDELT 观测时间，应另报 `provider_seen_at`，不能把它冒充原站 `published_at`；本轮不改
collector、不改数据库、不重跑实网。

## 失败项

- **NVIDIA Blog**：`ConnectTimeout`，20.75s 后失败，无 HTTP 状态。旧状态已是禁用；原因可能是区域性/临时网络问题，
  本轮不做重试或 URL 替换。
- **36氪快讯**：HTTP 200，但 `ValueError:INVALID_FEED`；响应体未保存，因此无法区分是反爬页面、空响应还是格式变化。
  不绕过访问控制、不猜测新地址；维持禁用。
- **NIH NIDCR Science News**：HTTP 404，`HTTP_404`。对应 GovDelivery 主题 feed 可能已下线或迁移；本轮未找替代 URL；
  维持禁用。

## 未确认项与时效边界

- 本轮只是**单时点快照**：不测量发布到发现的延迟，不证明内容持续更新，也不证明下一次尝试仍成功。
- HTTP 200/可解析不等于内容已更新，也不等于允许转载；使用范围仍以 [`SOURCE_POLICY.md`](SOURCE_POLICY.md) 中
  各官方条款为依据，本轮没有重新谈判或扩大任何许可。
- GDELT 历史失败原因、36氪返回内容、NIH 404 是否永久、NVIDIA Blog 超时是否区域性、arXiv 空列表原因，均未确认。
- 官方页面通常不承诺 SLA 或明确数字配额；未知项继续记为未知，不虚构限额。
- 本轮未对全部出版方条款逐页重读；只对 arXiv RSS 说明、GDELT DOC 说明与 GDELT Terms of Use 做了本次实际查看。
- 历史 `source-validation.json` 与 `config/sources.yaml` 均未改写；历史“22 个成功/启用”不能当作当前可用数。

## 访问政策依据（沿用 `SOURCE_POLICY.md`）

- [arXiv RSS 说明](https://info.arxiv.org/help/rss.html)：feed 每日批量更新；[API 使用条款](https://info.arxiv.org/help/api/tou.html)
  适用于 RSS；本轮执行所有 arXiv feed 共享至少 3.1s 间隔、串行、每源一次采集尝试。
- [NVIDIA RSS 目录](https://www.nvidia.com/en-us/about-nvidia/rss/)、[NASA 订阅目录](https://www.nasa.gov/rss-feeds/)、
  [EIA 订阅目录](https://www.eia.gov/tools/rssfeeds/)、[ECB 订阅目录](https://www.ecb.europa.eu/home/html/rss.en.html)、
  [FDA 订阅目录](https://www.fda.gov/about-fda/contact-fda/subscribe-podcasts-and-news-feeds)：仅使用官方目录给出的已登记地址，
  只保存标题/摘要/链接/时间等订阅元数据，不抓全文、图片或 PDF。
- [GDELT DOC 2.0 说明](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)与
  [GDELT Terms of Use](https://gdeltproject.org/about.html#termsofuse)：只把返回结果当发现元数据，原文章权利仍归出版者。
- 本轮没有新增来源或新 URL；所有请求地址均来自 `config/sources.yaml`。

## 复核脚本与 mock 测试

- 新增 [`scripts/recheck_sources.py`](../scripts/recheck_sources.py)：只读配置、可指定独立输出路径，拒绝写入
  `config/sources.yaml` 与 `docs/source-validation.json`；输出含每源 UTC 检查时间、HTTP/错误码、格式、条目数、
  字段覆盖、最近显式时间、旧 enabled；退出码 0=全部解析、1=部分空/失败、2=无任何解析、3=配置/输出/完整性错误。
  修复后脚本报告 schema 2；已有实网 JSON 仍为 schema 1 原始证据。
- 新增 [`tests/test_recheck_sources.py`](../tests/test_recheck_sources.py)：初测 18 个 mock 测试；安全返修后新增
  15 个回归用例（sidecar/符号链接、运行中输出竞争、--force、nan/inf/负间隔、非法 source_type、IO 短错误、
  临时文件清理等），**共 33 个通过**。覆盖 RSS/GDELT 成功、空列表、损坏 feed、超时、HTTP/传输错误、
  部分/总失败、保护路径、配置变化检测、arXiv 3.1s 与同域间隔、每源一次采集尝试、UTC 时间戳、不泄漏正文；
  `ruff check`/`ruff format --check` 通过。返修后全套 pytest 为 **143 passed**（含本文件 33 项），Ruff 共检查 71 个文件通过。
- 真实请求只执行了初测这一轮；返修后没有重跑实网。没有运行 bootstrap/worker/scheduler/Compose/看板，
  没有使用项目 AI/通知，没有 commit/push。

## 待 Codex 审查

1. GDELT 本次成功但历史失败未归因，且延迟接近 20s HTTP 阶段超时：是否继续保持禁用并安排后续观察。
2. arXiv 四个空 feed 原因未知：是否安排工作日单次复核后再判断健康。
3. 本轮已更新 README、`SOURCE_POLICY.md`、`LIMITATIONS.md` 的当前口径；请审查措辞是否准确。
4. 是否需要在后续版本单独增加 `provider_seen_at` 概念；本轮不改 collector/DB。
