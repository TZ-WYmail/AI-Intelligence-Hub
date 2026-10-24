# 事件关联误合并修复记录（阶段二）

## 范围与结论

阶段二只处理“不同事实被误合并”的问题，不扩展到推荐、UI、AI 分析或通知策略。

结论：已移除 fuzzy 近似阈值，事件关联改为**严格规范化标题相等**。规范化只处理可以确信为排版的差异（Unicode 规范等价、大小写、空白、句末感叹号），保留词序、数字、正负号、Unicode 负号、币种、否定、问号、产品符号和技术标识。无法确认时返回不关联，宁可漏关联。

本次只修改：

- `app/processing/event_matcher.py`
- `tests/test_event_linking.py`
- `scripts/benchmark.py`（Codex 追加授权）
- 新建本文件 `docs/EVENT_LINKING_REVIEW.md`

未修改其他会话负责的 `app/main.py`、`app/services/queue.py`、`dashboard/Home.py`、`tests/test_dashboard.py`、`tests/test_job_recovery.py`、`tests/conftest.py`、视觉简报测试、`docs/V1_ITERATIONS.md`；未修改 `docs/benchmark.json`。

## 根因

1. 旧 `normalized_title` 先 `NFKC` 再 `\w+` 提取，`-`、`+`、`$`、`€`、`?`、`++` 等符号全部被删除。  
   `-10%` 与 `+10%`、`$10M` 与 `€10M`、`S26+` 与 `S26`、`C++` 与 `C`、问句与陈述句都可能得到同一个 token 串。
2. 旧实现用 `set(PROTECTED.findall(...))` 和 `set(words)` 比较。set 丢掉词序，无法发现实体主客体换位；数字集合相同也掩盖了金额/数量角色互换。
3. 旧实现再用 `SequenceMatcher` 做模糊阈值匹配（`>= 0.94`），标题越相似越容易跨过阈值，但标题相似并不证明事实相同。
4. 误合并后，第二篇报道不再生成独立 Event / 通知；一次错误关联会抑制后续提醒，并污染事件计数和吞吐报告。

## 当前策略

`normalized_title` 依次执行：

1. Unicode `NFC`，**不使用 NFKC**。  
   NFKC 会把 `①`/`1`、`²`/`2`、`Ⅳ`/`IV`、全角 `Ｃ`/`C` 等兼容字符折叠，这些字符可能属于不同技术/产品标识。NFC 只做规范等价，不做兼容折叠。
2. `str.lower()`，**不使用 casefold**。  
   casefold 会把 `ß` 展开为 `ss`、`ﬀ` 展开为 `ff`，从而把不同拼写或标识当成同一事实；`lower()` 保留这些字符，只去除大小写差异。ASCII 大写标题（现有 `TITLE.upper()` 正例）仍然等价。
3. 合并连续空白并去掉首尾空白。
4. 去掉句末感叹号及其前面的空白（`Title!` == `Title`，`Title!!!` == `Title`）。  
   问号不删除；`?` 是事实/语气信号。其他标点符号一律保留。
5. 其余不做任何处理：不删标点、不排序、不做 token set、不做数字 set、不做模糊打分。

`title_similarity` 现在只返回 `1.0` 或 `0.0`，仅用于兼容现有调用/测试契约；`matching_event` 不再使用任何相似度阈值。

保留的保守门槛与既有不变量：

- 72 小时跨来源窗口不变。
- `publication_time_inferred=True` 的报道不参与关联。
- 同一来源已出现在事件中时，后续报道不合并。
- 候选上限 500、标题长度上限 600 不变。
- 关联关系仍为 `similar_title_unverified`，不会升级 `verification_status`。
- 多个既有事件同时命中时返回 `None`，不任意挑选。
- 不修改已有历史事件；本次只影响新报道是否挂到既有事件。
- 跨来源且标题排版等价的既有正例仍合并（例如大小写 + 末尾 `!`）。

## Codex 复现用例前后对比

旧分数为按 git HEAD 旧实现重放得到的 `title_similarity`；新分数为当前实现。

| 用例 | 旧分数 | 旧是否关联 | 新分数 | 新是否关联 |
| --- | ---: | :---: | ---: | :---: |
| 金额 10M/20M 与 20 家/10 家换位 | 0.9683 | 是 | 0 | 否 |
| `-10%` vs `+10%` | 1.0000 | 是 | 0 | 否 |
| Unicode 负号 `−10` vs ASCII `-10` | 1.0000 | 是 | 0 | 否 |
| `$10M` vs `€10M` | 1.0000 | 是 | 0 | 否 |
| 短标题主客体换位 | 0.8267 | 否 | 0 | 否 |
| 长标题主客体换位 | 0.9557 | 是 | 0 | 否 |
| 陈述句 `...Europe` vs 问句 `...Europe?` | 1.0000 | 是 | 0 | 否 |
| `C++` vs `C` 产品标识 | 1.0000 | 是 | 0 | 否 |
| `S26+` vs `S26` 产品标识 | 1.0000 | 是 | 0 | 否 |
| `Straße` vs `Strasse` | 1.0000 | 是 | 0 | 否 |
| `oﬀice` vs `office` | 1.0000 | 是 | 0 | 否 |
| 排版等价：大小写 + 末尾 `!` | 1.0000 | 是 | 1.0 | 是 |

“旧是否关联”按旧 `matching_event` 的 `>= 0.94` 阈值判断。`Straße`/`Strasse` 与 `oﬀice`/`office` 是 Codex 中期 review 指出的 casefold 折叠问题，当前用 `lower()` 修复。

## 新增回归覆盖

`tests/test_event_linking.py` 新增参数化用例：

- 金额与数量换位、正负号、Unicode 负号、币种。
- 短标题和长标题的实体主客体换位。
- 否定、问句、数字差异。
- 产品符号：`C++`/`C`、`S26+`/`S26`。
- 中文数字角色换位、中文实体主客体换位。
- 超长标题（601 字符相同标题）不关联；600 上限行为保留。
- 排版正例：大小写、连续空白、末尾一个/多个 `!` 仍关联。
- 兼容字符不折叠：`①`/`1`、全角 `Ｓ`/`＋` 与 ASCII 不合并。
- 德语 `ß`/`ss` 与 `ﬀ`/`ff` 拼写不合并。

pipeline 集成测试（参数化 8 组不同事实）：

- 两篇相似但事实不同的报道生成 2 个不同 Event。
- 保留 2 条 Article、2 条 Analysis，且 `Analysis.event_id` 不同。
- 两条 `EventArticle` 关系均为 `initial`，未误标 `similar_title_unverified`。
- 两个 Event 均保持 `unverified`、`version=1`。
- 通知使用 mock Telegram 配置，只检查持久化队列：2 条 Notification 均为 `pending`、2 个 `notify` Job 均为 `pending`，`WebhookNotifier.send` 未被调用（未发送）。
- 原跨来源同标题合并、缺失日期不合并、同来源后续不合并、4 天前旧报道不合并等已有断言保留。

实际执行：

```text
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest -q tests/test_event_linking.py
35 passed, 1 warning in 0.32s
```

Ruff 只检查本次负责的文件：

```text
.venv/bin/ruff check app/processing/event_matcher.py tests/test_event_linking.py scripts/benchmark.py
All checks passed!

.venv/bin/ruff format --check app/processing/event_matcher.py tests/test_event_linking.py scripts/benchmark.py
3 files already formatted
```

## Benchmark 报告修正

`scripts/benchmark.py` 增加：

- `events` 数量。
- `unfinished_jobs`：`pending` / `retry_wait` / `running` 数量。
- 保留 `failed_jobs`。
- 对该 fixture（100 来源 × 30 篇，每篇来源 URL 与索引标题都不同，明确代表独立事件）断言：
  - `articles == 3000`
  - `events == 3000`
  - `notifications_sent == 3000`
  - `unfinished_jobs == 0`
  - `failed_jobs == 0`
- 校验失败时打印不匹配项并以非零状态退出，且**不写** `docs/benchmark.json`，避免把部分吞吐报告发布成验收成功。
- 只有全部校验通过才写 `docs/benchmark.json`。

按要求本次没有长跑 benchmark，留待 Codex 在阶段三统一执行；`docs/benchmark.json` 未改动。

## 边界与剩余风险

当前策略刻意选择漏关联：

- 仅标点/空格排版不同（例如 `$ 10M` vs `$10M`、全角标点、Unicode 负号 vs ASCII 连字符）不会关联，除非属于上述明确规范化范围。
- 非 ASCII 大小写只做 `lower()`，不做 casefold；`Straße`/`Strasse`、`ﬀ`/`ff` 等拼写差异保持不合并。
- 大小写整体被视为排版差异；如果产品标识本身区分大小写（例如 `pH`/`PH`），仅大小写不同仍会合并。这是保留现有 `TITLE.upper()` 正例所接受的风险。
- NFC 仍会做规范等价折叠（例如 `K` 与 `K`），这是 Unicode 规范等价而非兼容折叠。
- 短于 24 个规范化字符的标题不关联；超过 600 字符的标题不关联。
- 500 候选上限和 72 小时窗口不变；窗口外或候选过多时可能漏关联。
- 多个既有事件同时命中时返回 `None`，新报道会新建事件，可能产生重复事件，但不会错误合并。
- 句末 `!` 被视为排版修饰；如果品牌/标识本身以 `!` 结尾，可能与无 `!` 写法关联。
- 本次未扩大测试范围、未跑全量套件、未跑 benchmark、未启动任何服务、未使用真实数据库/凭据/AI/通知、未 commit/push。
