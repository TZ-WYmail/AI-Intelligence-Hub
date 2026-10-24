# V1 稳定性与体验打磨（2026-10-19）

本轮为 V1 稳定性收敛，不做功能扩展。基线：`51 passed`。审计与回归均使用临时 SQLite、mock 通知，不启动服务、不发送真实消息、不调用外部模型。

## 问题 1：P1 批量通知失败会污染后续批次

- **触发条件**：`notify_batch` 任务在发送时异常（如渠道 5xx、收件人配置变化），且任务重试耗尽转为 `failed`。
- **原因**：worker 异常分支按“同渠道全部未发送 P1”更新通知，没有使用当前任务的 `cutoff`，因此会把尚未到发送时间的后续批次一并标为 `failed`。
- **影响**：后续批次通知永久不再发送，通知记录状态与真实投递机会不一致。
- **修复**：在 `app/services/queue.py` 增加统一的 `notifications_for_job`，发送、失败同步和租约恢复共用同一选择条件：渠道、P1、`pending/retry_wait`、`created_at <= cutoff`。worker 失败分支只同步本任务范围；重试时同步为 `retry_wait`，最终失败才标为 `failed`。
- **回归**：`test_notify_batch_failure_only_marks_notifications_in_its_cutoff`，断言旧批次失败后，后续批次仍为 `pending` 且无错误标记。

## 问题 2：租约过期的最终失败缺少任务与通知状态同步

- **触发条件**：Worker 在通知任务执行中退出，任务租约过期，且领取次数已达上限。
- **原因**：`recover` 将任务标为 `failed` 时只清空 `dedup_key`，未写 `finished_at`；对应通知仍停在 `pending`，不会再有任务投递。
- **影响**：任务审计时间不完整，通知永久悬挂，运维无法判断已经最终失败。
- **修复**：`recover` 最终失败时补写 `finished_at`；通过同一范围函数同步通知为 `failed`、`last_error=LEASE_EXPIRED`。租约恢复为可重试时同步为 `retry_wait`。批量任务仍受自身 `cutoff` 限制。
- **回归**：`test_recover_terminal_notify_syncs_job_and_notification_failure`、`test_recover_terminal_batch_respects_cutoff`。

## 审查修正 1：坏任务参数不得阻断恢复，也不得静默完成

- **发现**：首轮修复让 `recover` 在同步通知状态时解析 `notify_batch.payload.cutoff`。若该值为 `not-a-time`，`ValueError` 会从 `recover` 抛出，而 `worker.tick` 此时尚未进入单任务 `try`，导致本 tick 后续正常任务全部不执行；同时缺失 `notification_id` 等必需参数的通知任务因“无通知”被直接标为 `completed`，错误被静默吞掉。
- **修复**：在 `app/services/queue.py` 增加 `payload_error(job)` 与 `InvalidJobPayload`，按任务类型校验必需参数（`collect.source_id`、`analyze.article_id`、`notify.notification_id`、`notify_batch.channel/cutoff` 是否可解析、`daily_digest.start/end`）。`recover` 对坏参数任务只把该行安全标为 `failed`，写简短错误码（如 `INVALID_CUTOFF`、`MISSING_NOTIFICATION_ID`）、`finished_at` 并释放 `dedup_key`，然后继续处理同批其他任务；坏参数下通知范围不可知，因此不猜测更大范围、不修改任何通知。`dispatch` 同样在入口显式抛出 `InvalidJobPayload`，由 worker 失败分支按终态处理，不消耗剩余重试；`sync_notification_state` 对坏参数直接返回，避免 worker 正常异常路径因同一坏参数再次抛错而回滚已写入的失败记录。collect 失败记账在 `source_id` 缺失或来源已不存在时跳过来源更新，避免记录失败时再抛 `AttributeError`。数据库/连接等真实故障仍照常向外抛出，未被宽泛吞掉。
- **回归**：`test_corrupt_lease_payload_isolated_and_failed`（同一 tick 内坏任务失败且后续 `collect` 仍执行、无关通知保持 `pending`）、`test_missing_notify_payload_is_failed_not_silently_completed`、`test_bad_collect_job_fails_without_blocking_next_job`（坏 collect 自身失败，下一正常 collect 继续执行；修复前该测试实际失败）。

## 审查修正 2：analyze 租约最终失败遗留 `started` 分析

- **发现**：analyze 最后一次尝试中进程退出，租约恢复为 `failed` 后，关联的 `openai-compatible` 且 `status='started'` 的 `Analysis` 永久停留在 `started`，审计与用量视图失真。
- **修复**：`recover` 在 analyze 任务终态失败时调用 `interrupt_started_analysis`，仅把该文章下仍为 `started` 的 openai-compatible 分析标为 `interrupted`；保留 `estimated_cost`（供应商可能已计费，且该预留仍占用当日请求数与月度预算），不触碰 `completed`/`failed` 历史分析。可重试恢复路径仍由 `pipeline.analyze` 在下次执行时收尾，不在此重复处理。
- **回归**：`test_recover_terminal_analyze_interrupts_started_usage`（断言 `interrupted`、`estimated_cost` 不变、历史 `completed` 不变）。
- **边界回归**：`test_recover_never_downgrades_or_resends_sent_notification`，已 `sent` 的通知在租约恢复后保持 `sent`/`sent_at`，不写错误、不被降级，且不产生新任务重发。

## 已核查但不改动

- 行业 PATCH 传入非法字段/空名称/越界阈值时，`main.py` 的全局 `ValidationError` 处理器返回 422；已用 TestClient 复现，与来源 PATCH 行为一致，无需修改。
- 来源 PATCH 的非法轮询间隔、私网 URL、缺失行业均返回 422；日期参数无时区、分页参数越界、报告时间范围倒置均返回 422，未发现需在本轮修改的缺陷。

## 验收标准与结果

- 首轮新增 3 个针对性 mock 回归测试，先复现失败，再修复通过。
- 审查修正轮新增 5 个回归（含 Codex 独立复现的两例、`sent` 边界与坏 collect 记账边界）。其中坏 cutoff 阻断、`started` 分析遗留、坏 collect 失败记账三例修复前已实际复现（`ValueError` 阻断同 tick 其他任务、`started` 分析永久悬挂、`AttributeError` 打断失败落盘），修复后通过；缺失通知参数一例覆盖修复前会被静默标为 `completed` 的路径，`sent` 一例作为“已投递不降级、不重发”的边界守护。
- `env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest -q` → `59 passed`（基线 54 项，新增 5 项；沙箱内 uv 缓存只读，故直接用仓库 venv 运行同一套测试）。
- `.venv/bin/ruff check .`、`.venv/bin/ruff format --check .` → 通过。
- 未启动 Compose/采集/调度/通知，未使用真实凭据，未改写现有数据。

## 尚存风险与后续

- 历史上已经错误标记为 `failed` 的通知不会自动修复；如需修复应单独做数据核对，不在本轮自动改历史数据。
- 第三方无幂等键，外部已接收但状态未落盘时仍可能重复发送；Lease 恢复不改变该边界。
- 多 Worker 的租约与预算语义仍不支持；SQLite 单 Worker 部署假设保持不变。
- 通知状态 `retry_wait` 现在也会用于批量通知，界面按原始值展示，后续可补状态中文映射。

# 任务恢复 API 与运行日志体验（2026-10-24）

本轮接续网络中断遗留的未提交改动，完成失败任务重试 API 与「运行日志」页验收。硬约束：只用临时 SQLite 与 mock，不启动 API/Worker/Scheduler/看板/Compose，不读取或输出 `.env`/凭据，不调用真实模型或通知，不改真实数据库配置，不安装升级软件，不 commit/push；`docs/benchmark.json` 与 `app/processing/event_matcher.py` 未改动。

## 失败 1：运行日志重试选择使用截断 ID 作为键

- **现象**：`test_run_logs_filter_and_retry_actions` 断言完整 `failed-collect` 出现在选项里，UI 只显示 `id[:8]`。
- **根因**：`choices` 以 `f"{类别} · {id[:8]} · {错误}"` 为字典键。8 位前缀相同的两个任务会互相覆盖，其中一条从可重试列表中静默消失；测试则把截断标签当成唯一标识。
- **修复**：`dashboard/Home.py` 改为 `multiselect` 选项值就是真实任务 ID，`format_func` 只用完整 ID 生成展示标签。选择与提交都基于真实 ID，不再用截断字符串做键。
- **回归**：测试按真实 ID `set_value(["failed-collect", "failed-disabled-source"])`，断言 `multiselect.value` 为真实 ID，并确认重试后 `failed-collect` 变为 `pending`。

## 失败 2：网关非 JSON 错误断言了“无标题”

- **现象**：`test_dashboard_non_json_gateway_error_stops_safely` 断言 `not ui.title`。
- **根因**：(1) 测试把“页面空白”当成安全，而标题本身不泄露上游内容；(2) 页面在第一次 API 调用之后才渲染标题，网关 503 时标题也不会出现。
- **修复**：`Home.py` 在首个 API 调用（`settings`）之前渲染页面标题；非 JSON/5xx 仍只显示固定的安全提示并 `st.stop()`。
- **回归**：断言标题 `概览` 仍存在、错误文案精确为 `API 服务暂不可用（HTTP 503），请稍后重试。`、不含 `secret`，且错误后不再渲染业务数据（`metric`/`dataframe`/`subheader` 为空）。

## 失败 3：简报 UI 用例受进程级限流污染

- **现象**：`test_report_ui_pages_preserve_custom_results_and_reset_sizes` 单独运行通过、全套偶发失败；本次复现为「Entry 00」或「Custom 11」缺失。
- **根因**：`app/main.py` 的 `_requests` 是进程级、按客户端 host 计数的 240 次/分钟限流窗口。全套测试在数秒内共享同一 `testclient` host，累计请求超限后 API 返回 429「请求过于频繁」，简报页 `api()` 收到 4xx 后 `st.stop()`，后续 markdown 与分页控件全部不渲染。不是日期或 Streamlit 状态问题。
- **修复**：`tests/conftest.py` 新增 autouse fixture，每个测试前后清空该限流窗口，使测试互不影响；未放宽任何业务断言，也未修改生产限流逻辑。
- **回归**：全套连续多次运行稳定通过；失败前已用 `ui.session_state.to_dict()` 确认 `_custom_brief` 存在且页码为 3，页面上唯一的错误是 429。

## 审查修正 1：畸形 payload 与倒置日报窗口

- **问题**：`payload_error` 只校验 truthy。`article_id` 为 list/dict/int 时可通过校验，随后进入查询并返回 409（或潜在 500）；`daily_digest` 的 `end <= start` 也能重试成功。
- **修复**：新增 `identifier_error`、`timestamp_error`，先判断类型再解析：
  - `analyze.article_id` 非空字符串校验，畸形返回 `INVALID_ARTICLE_ID`；
  - `notify.notification_id` 同理返回 `INVALID_NOTIFICATION_ID`；
  - `notify_batch.channel` 只接受 `telegram`/`feishu`，`cutoff` 必须是可解析字符串；
  - `daily_digest.start/end` 必须是可解析字符串且 `end > start`，否则 `INVALID_DIGEST_WINDOW`；
  - 非 dict payload 返回 `INVALID_JOB_PAYLOAD`。
  重试 API 将参数类拒绝映射为 422、来源缺失/文章缺失为 409、来源停用为 409，均带 `X-Error-Code`，不再出现 500。
- **回归**：3 个畸形 `article_id`（list/dict/int）、2 个倒置/相等日报窗口、1 个非对象 payload，全部断言 422 且任务保持 `failed`、原 `attempt_count`/`last_error` 不变。

## 审查修正 2：不可哈希的 `notify_batch.channel` 阻断 recover

- **问题**：新增的 `channel not in {"telegram", "feishu"}` 在 JSON 值为 list/dict 时抛 `TypeError: unhashable type`，从 `recover` 抛出并阻断同一 tick 的所有后续任务。
- **修复**：先 `isinstance(channel, str)` 再做集合成员判断，非字符串返回 `INVALID_CHANNEL`；新增校验中没有其他对不可信 JSON 值直接做集合成员判断的路径。
- **回归**：3 个过期 `running` + 坏 channel 参数（list/dict/空 list），断言只有坏任务转为 `failed`，同一 tick 的有效 `collect` 仍执行。

## 审查修正 3：并发删除时 `ObjectDeletedError`

- **问题**：CAS `rowcount=0` 分支先 `rollback()`，随后 `session.get(Job, job.id)` 读取已被 rollback 过期的 ORM 对象属性；若该行已被 retention 并发删除，先抛 `ObjectDeletedError`，设计中的 404 无法返回。
- **修复**：`retry_failed_job` 进入时保存普通字符串 `job_id`，CAS 条件与 rollback 后的重新查询都只使用它，不再读取过期对象属性。行不存在时抛出 `JOB_NOT_FOUND`（404），与 Worker 抢占的 `JOB_STATE_CHANGED`（409）区分。
- **回归**：`test_retry_reports_concurrent_removal_as_not_found` 用第二个 session 删除行后调用服务函数；修复前实测抛 `ObjectDeletedError`，修复后返回 `JOB_NOT_FOUND`/404，行确已不存在。

## 保留的既有行为

- 重试仍使用 `update(... where status='failed')` 条件更新，不覆盖已被 Worker 抢占为 `running` 的行。
- 活跃业务键（`pending`/`retry_wait`/`running`）去重，重复点击幂等；通知任务仍禁止手动重试。
- 重试不重置 `attempt_count`、`last_error`，不改 `Analysis` 预算/用量；`scheduled_at` 设为当前时间。
- 界面批量重试逐条调用 `soft=True` 的 API：单条失败不再 `st.stop()` 吞掉已入队结果，分别汇总「已入队 / 已去重 / 失败（含任务 ID 与原因）」；测试覆盖一条成功、一条来源停用的部分失败场景。

## 验收结果

- `env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest -q` → `110 passed`（起始 67 passed/3 failed；本轮新增 10 个 mock 回归：3 畸形 `article_id`、2 倒置/相等日报窗口、1 非对象 payload、1 并发删除、3 不可哈希 channel）。期间 Codex 已单独提交事件关联修复并扩充事件用例，本任务未修改 `event_matcher`。
- `.venv/bin/ruff check .`、`.venv/bin/ruff format --check .` → 通过。
- 未启动服务、未使用真实凭据、未写入真实数据库、未 commit/push；`docs/benchmark.json` 未改动。
- 起始的 3 个失败均已按根因修复（截断 ID 键、标题断言、进程级限流隔离），未删除或弱化行为覆盖。
