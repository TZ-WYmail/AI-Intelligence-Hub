# 第五段 Soak 验收记录（进行中）

> ## ⚠️ 状态：第五段 24 小时观察进行中，尚未验收
>
> 当前只能记录“已通过什么”和“还缺什么”。**本文档不是验收通过证书**，也不能
> 把中期检查点或短程结果当作 24 小时完成。唯一最终依据是：同一容器的退出证据、
> 真实 `summary.json`、连续性/隔离断言，以及只读数据库与本地 mock 通知 JSONL
> 的对账结果。
>
> 截至本文档编写时：24 小时未满，`summary.json` 不存在；整体结论保持
> **待验收（pending）**。

## 1. 审查与运行身份

| 项目 | 实际值 |
| --- | --- |
| 已审查提交 | `429779a`（完整 `429779ad15ee432f92ea6b40bd0c6a100b919008`） |
| 镜像 tag | `aih:soak-429779a` |
| 镜像 ID | `sha256:e391f0908fc48ec2a32986e5aafcbf3f20cff16a93a0c1746f9c08699de38c51` |
| 长程容器名 | `aih-soak-429779a-6dd86de1` |
| 长程容器 ID | `898cb0ca19f0f7e6a029268e5be0bf5c59ed3cd28988924b58426ebd1700ef55` |
| 长程 run_id | `container-24h-429779a-6dd86de1` |
| 宿主 run 目录 | `data/soak/container-24h-429779a-6dd86de1/` |
| 容器内 run 目录 | `/srv/aih/data/soak/container-24h-429779a-6dd86de1` |
| 请求时长 / 间隔 | `86400` 秒 / `60` 秒（单容器，仅此一个长程实例） |
| 启动（UTC / 北京） | `2026-10-24T17:05:42Z` / `2026-10-25 01:05:42` |
| 预计达到 86400 秒（UTC / 北京，估算） | `2026-10-25T17:05:42Z` / `2026-10-26 01:05:42` |

预计结束时间只是按 `started_at + 86400` 估算；是否真正达到请求时长，必须以
`summary.json` 的 monotonic `observed_seconds` 和 UTC 起止为准。

## 2. 已通过事实：短程隔离探针

短程容器使用同一镜像、`--network none --read-only --cap-drop ALL
--security-opt no-new-privileges --user 1000:1000`，仅绑定 `data/soak`，完成后
以 `--rm` 移除；宿主保留完整证据目录。

| 项目 | 结果 |
| --- | --- |
| run_id | `container-short-429779a-a128c51a` |
| 请求时长 / 批次 | 10 秒 / 40 批 |
| 实际运行 | `observed_seconds=10.134` |
| 状态 | `completed`，`error_code=null` |
| 计数 | `articles=events=notifications_created=notifications_sent=200` |
| 重复 | `duplicate_rows_inserted=0`，`unique_article_sets_match=true` |
| mock 429 | `observed=true`、`recovered=true`（第 2 批） |
| 租约恢复 | `status_before=running`、`status_after=completed`、`recovered=true` |
| 关库重开 | `performed=true`、`integrity_check=ok`、前后行数保持 |
| 隔离 | `network_attempts=0`、`failed_jobs=0`、backlog 全 0、`unexpected_errors=[]` |
| 连续性 | `continuity.ok=true`，`cadence_violations=[]` |
| 24 小时标志 | `full_soak_completed=false` |

证据：`data/soak/container-short-429779a-a128c51a/summary.json`。

短程通过只证明代码路径、隔离参数和完成断言在该次短程中成立，**不证明 24 小时
连续运行**，不能替代最终验收。

## 3. 中期独立复核快照（不是实时数据）

Codex 已在北京时间 **2026-10-25 13:23** 独立核对 Docker 隔离、镜像内 46 个
代码/配置文件的哈希，以及只读模拟数据库。该时点的快照为：

- checkpoint：**738 批**
- `observed_seconds=44220.560`（约 12 小时 17 分）
- `articles=events=notifications=ai_analyses=3690`
- `jobs`：**11071 个 completed**
- 本地 mock 通知：`sent=3690`
- AI 分析 provider：全部为 **rules**（规则分析，不是真实模型调用）
- 只读模拟数据库：`PRAGMA integrity_check=ok`
- 网络尝试、失败 job、积压：**均为 0**
- 连续性：正常

以上是**某一时点的快照，不是实时状态，也不是最终结果**。批次数、文章数和通知数
会随后续批次继续增长；不得把该快照中的数字写死为最终验收值，也不得据此宣布
24 小时通过。

本文档写入前又做了一次只读抽查：同一容器仍为 `Running=true`、`Paused=false`、
`RestartCount=0`；抽查当时 checkpoint 为 **739 批 / 44280.533 秒**（约北京时间
13:23:42），`summary.json` 仍不存在。该抽查同样只是快照。

### 追加：13:24:55 的 13 项只读对账

Codex 又于北京时间 **2026-10-25 13:24:55** 完成一次独立的只读对账，原始记录：
`data/codex-midpoint-review.json`（其中 `full_24h_accepted=false`）。该次快照为
**740 批 / 44340.598 秒**，13 项结果如下：

1. checkpoint 与 DB/JSONL 的批次计数一致：740 批、44340.598 秒。
2. `articles=3700`，`notifications.jsonl=3700` 行。
3. 文章 `(来源, external_id, URL)` 的预期集合与实际集合完全一致。
4. 文章 hash 唯一。
5. 分类（classification）已完成。
6. 所有事件保持 `unverified`。
7. 每篇文章恰好对应一个事件。
8. 通知全部为 `sent`，mock 投递每篇仅一次。
9. 分析 provider 全部为 `rules`。
10. 分析 tokens 与 cost 均为 0。
11. `jobs=11101`，全部 `completed`。
12. `integrity_check` 与 FK 检查正常。
13. 重复输入 3695 条，未产生额外文章行。

该对账使用只读 SQLite（`mode=ro` + `query_only`）事务快照，并保持 DB/JSONL
读取期间的 checkpoint 批次稳定；未导入应用，也未发出外部请求。它仍是**中期
证据，不是实时状态，不能替代 24 小时最终验收**。

## 4. 隔离方式

当前长程容器使用以下隔离，验收时需确认这些条件未被替换或放宽：

- `--network none`；无真实外部网络路径。
- `--read-only` 根文件系统；`/tmp` 为 `rw,nosuid,noexec,size=64m` 的 tmpfs。
- `--cap-drop ALL`；`--security-opt no-new-privileges`。
- `--user 1000:1000`（本机 uid:gid）。
- 唯一 bind：宿主 `data/soak` → 容器 `/srv/aih/data/soak`；不挂整个项目、
  生产卷、`/home`、`.env` 或 `docker.sock`。
- `--detach --init --cpus 1 --memory 512m --pids-limit 64`；无自动重启，
  不使用 `--rm`，以保留退出证据。
- 容器工作目录 `/srv/aih`；执行
  `python -m scripts.soak --duration-seconds 86400 --interval-seconds 60 --run-dir /srv/aih/data/soak/container-24h-429779a-6dd86de1`。
- 只使用合成 RSS、本地 mock Notifier 和进程内 NetworkGuard；不调用真实模型、
  Telegram/飞书、外部 RSS/GDELT 或任何通知渠道。
- 容器内 PID 不是宿主 PID；检查点中的 `pid` 只在容器 PID 命名空间内有意义，
  不能用宿主 `kill -0` 判断存活。存活判断见 `docs/SOAK.md` 的容器检查点章节。

## 5. 限制（不能外推为生产结论）

- 这是合成数据的受控观察，不是生产 SLA，也不是真实第三方稳定性验证。
- 不覆盖真实 RSS 格式漂移、第三方限流/延迟、真实凭据、Telegram/飞书行为。
- mock 429 是 collector 注入；租约恢复是合成 stale 行；关库重开是主动
  `engine.dispose()` 后重开，不是 OS 崩溃恢复或进程重启演练。
- 单 worker、低负载；不覆盖并发领取、双 worker、SQLite 写锁竞争或 controller
  重启。
- 容器隔离不等于宿主机断电、内核崩溃、Docker daemon 故障或强制杀进程；
  这些场景没有得到验证。
- 连续性依赖 monotonic gap 与墙钟 gap/drift；阈值内的短暂停仍可能通过。
- 因此，即使未来 24 小时合成观察通过，也只证明“隔离容器内的合成 24 小时
  路径”通过，**不替代生产稳定性或真实服务验证**。

## 6. 最终验收清单（全部满足才可通过）

最终检查必须在容器退出、`summary.json` 已生成后执行。以下各项当前均无最终
证据的，保持“待验收”。

### A. 容器身份与退出

- [ ] 使用精确容器名/完整 ID 检查，仍是同一容器；镜像 ID 仍为
      `sha256:e391f0908fc48ec2a32986e5aafcbf3f20cff16a93a0c1746f9c08699de38c51`。
- [ ] `State.Running=false`、`State.Paused=false`、`State.ExitCode=0`、
      顶层 `RestartCount=0`（`docker inspect` 顶层字段，不是
      `State.RestartCount`）、`State.OOMKilled=false`、`State.Error` 为空。
- [ ] `HostConfig.AutoRemove=false`（证据仍可检查）；`RestartPolicy=no`。
- [ ] 没有第二个长程容器、没有替换容器、没有复用 run 目录。
- [ ] `summary.run_id` 与 run 目录 basename 一致；`summary.run_dir` 为容器路径
      `/srv/aih/data/soak/<run_id>`（不要拿它和宿主路径直接比较）。
- [ ] `summary.pid` 仅作容器内 PID 记录，不能用于宿主 `kill -0`。

### B. 真实时长与 UTC 起止

- [ ] `clock=monotonic`。
- [ ] `duration_seconds=86400`，且 `observed_seconds >= 86400`；最终真实时长
      只以 monotonic 为准。
- [ ] `started_at`、`updated_at` 均为带 UTC 偏移的时间，且 UTC 起止与
      monotonic 时长合理一致；允许 NTP 校时、取整和收尾写入造成的秒级小偏差。
- [ ] UTC 与 monotonic 不得出现无法解释的小时级缺口或数量级偏差；出现这类
      偏差时保持待复核，不能忽略，也不能仅凭 UTC 判定通过。
- [ ] UTC 起止与预计窗口大致一致；不得把北京时间误当 UTC。
- [ ] 只有真实 monotonic 达到 86400 秒才算 24 小时，mock 时钟/短程/stopped
      一律不算。

### C. 最终状态

- [ ] `status=completed`。
- [ ] `full_soak_completed=true`。
- [ ] `completion_scope=full_86400_target`。
- [ ] `error_code=null`，`failed_checks=[]`。

### D. 连续性

- [ ] `continuity.ok=true`。
- [ ] `continuity.cadence_violations=[]`。
- [ ] `max_gap_seconds`、`final_gap_seconds` 均不超过 `allowed_gap_seconds`。
- [ ] `max_wall_gap_seconds`、`final_wall_gap_seconds` 均不超过
      `allowed_gap_seconds`。
- [ ] `max_wall_drift_seconds`、`final_wall_drift_seconds` 均不超过 5 秒。
- [ ] 没有暂停、重启或“恢复后快速补跑”掩盖的缺口。

### E. 零外部调用与隔离字段

- [ ] `network_attempts=0`，`network_surfaces=[]`。
- [ ] `ai_model_calls=0`；没有真实 `openai-compatible` 模型调用。
- [ ] `isolated.env_file_disabled=true`、`isolated.private_database=true`、
      `isolated.settings_shared=true`、`isolated.network_guard_active=true`。
- [ ] `isolated.ai_enabled=false`。
- [ ] 没有真实通知渠道发送；通知只落在本地 `notifications.jsonl`。

### F. 失败、积压与意外错误

- [ ] `failed_jobs=0`，`failed_job_errors=[]`。
- [ ] `backlog.pending=0`、`backlog.retry_wait=0`、`backlog.running=0`。
- [ ] `source_errors=[]`，`unexpected_errors=[]`。
- [ ] `soak.log` 最后事件与 summary 一致，没有未解释异常。

### G. 计数与重复集

- [ ] `articles = events = notifications_created = notifications_sent =
      mock_notifications_delivered`。
- [ ] `notifications_sent = notifications_created`。
- [ ] `duplicate_checks.articles_expected = articles_actual = articles`。
- [ ] `duplicate_checks.duplicate_rows_inserted=0`。
- [ ] `duplicate_checks.unique_article_sets_match=true`。
- [ ] `duplicate_checks.batches_with_duplicate_feed_items>0`（确实观察过重复）。

### H. 三项受控恢复

- [ ] mock 429：`failure_recovery.observed=true`、
      `failure_recovery.recovered=true`，且 `expected_failures` 对应项一致。
- [ ] 租约：`lease_recovery.recovered=true`、`status_before=running`、
      `status_after=completed`。
- [ ] 关库重开：`db_reopen.performed=true`、`db_reopen.integrity_check=ok`、
      `db_reopen.committed_rows_preserved=true`，且 `before`/`after` 的
      articles/events/notifications/jobs 行数一致。

### I. 只读数据库与 mock 通知 JSONL 对账

容器退出后，以只读方式打开合成数据库，不得写入、迁移或修复：

```bash
RUN_DIR=data/soak/container-24h-429779a-6dd86de1
sqlite3 "file:$RUN_DIR/soak.db?mode=ro" "PRAGMA integrity_check;"
sqlite3 -header -column "file:$RUN_DIR/soak.db?mode=ro" "
select 'articles' as item, count(*) as n from articles
union all select 'events', count(*) from events
union all select 'notifications', count(*) from notifications
union all select 'ai_analyses', count(*) from ai_analyses
union all select 'jobs_completed', count(*) from jobs where status='completed'
union all select 'jobs_other', count(*) from jobs where status<>'completed';"
wc -l "$RUN_DIR/notifications.jsonl"
```

- [ ] `PRAGMA integrity_check=ok`。
- [ ] articles/events/notifications 行数与 summary 计数一致。
- [ ] jobs 全部为 `completed`，没有 failed/pending/retry_wait/running。
- [ ] `notifications.jsonl` 行数与 `notifications_sent` /
      `mock_notifications_delivered` 一致。
- [ ] `ai_analyses` 与 articles 的对应关系已记录并解释；本轮为 rules provider，
      同时必须保持 `ai_model_calls=0`。

### J. 证据保留

- [ ] 保留 `checkpoint.json`、最终 `summary.json`、`soak.log`、
      `notifications.jsonl`、`soak.db` 及 Docker inspect 证据。
- [ ] 不删除、不覆盖、不复用该 run 目录；不修改旧验收数据。

### 判定规则

**未结束、异常退出、缺少 `summary.json`，或以上任一项未满足/未核实，均不得
通过。** 容器 `ExitCode=0` 也不能单独作为通过依据（`stopped` 也可能以 0 退出），
必须以 summary 的 `status`、`full_soak_completed` 和全部断言为准。

只有 A–J 全部通过，才能把整体状态写成“验收通过（合成 24 小时）”；否则保持
“待验收”或明确写“未通过”，并保留现场与短错误码。不得用部分通过、短程结果或
中期快照替代最终结论。

## 7. 当前状态

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| 已审查提交 `429779a` | 已审查 | Codex 独立审查结论 |
| 短程隔离探针 | 通过（仅短程） | 10.134 秒 / 40 批 / 200 文章等；`full_soak_completed=false` |
| 长程容器与隔离 | 运行中 | 同一容器 `aih-soak-429779a-6dd86de1`；无重启 |
| 24 小时时长 | 未满 | 中期快照 44340.598 秒（13:24:55 复核） |
| `summary.json` | 不存在 | 预计北京时间 2026-10-26 01:05:42 后才可能生成 |
| 最终验收清单 A–J | 待验收 | 尚无最终证据 |
| 生产稳定性/真实服务 | 不验证 | 本观察不能替代 |
| **整体结论** | **待验收（进行中）** | 不得称为 24 小时通过 |

## 8. 证据路径与适用范围

- 宿主证据目录：`data/soak/container-24h-429779a-6dd86de1/`，包含
  `checkpoint.json`、最终 `summary.json`、`soak.log`、`notifications.jsonl`
  和 `soak.db`。
- 容器身份与隔离证据：`docker inspect aih-soak-429779a-6dd86de1`。
- 中期独立对账原始记录：`data/codex-midpoint-review.json`。
- 适用范围：仅本次第五段隔离容器内的合成 24 小时观察。即使最终 A–J 全部
  通过，也不替代生产稳定性、真实第三方服务、真实模型或真实通知渠道验证。
