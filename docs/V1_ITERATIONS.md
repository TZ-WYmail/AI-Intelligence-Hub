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
