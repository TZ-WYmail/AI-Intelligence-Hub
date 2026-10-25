# 隔离合成 Soak（第五段）

本工具只做一件事：在**全新私有目录**里，用合成 RSS 驱动仓库已有的
`app.collectors.rss` → `app.processing` → `app.services.queue` → `app.workers.run`
→ SQLite 通知状态机，观察重复文章、受控 429、队列租约、关库重开和停止语义。

> 这不是生产 SLA，也不是真实第三方稳定性验证；不证明 24 小时真实运行。
> 短程完成只证明代码路径与断言，不能替代 Codex 审查后启动的长程观察。
>
> **第五段 24 小时观察进行中，尚未验收**。中期快照与最终验收清单见
> [`SOAK_ACCEPTANCE.md`](SOAK_ACCEPTANCE.md)；在 `summary.json` 出现并逐项满足
> 断言前，不得称为 24 小时通过。

## 不做什么

- 不连接真实模型、Telegram/飞书、外部 RSS/GDELT 或任何第三方接口。
- 不启动正式 API/worker/scheduler/dashboard，不执行 Compose，不调用迁移。
- 不读取或修改生产数据库、`.env` 里的凭据、生产配置。
- 不做通用 controller resume、多 worker 并发、真实 OS 崩溃或生产重启演练。
- GDELT、AI provider、日报、备份等不在本段范围。

## 隔离方式

`scripts/soak.py` 在导入 `app.db` / `app.workers.run` / `app.services.pipeline`
**之前**完成以下步骤：

1. 写入合成环境配置（`AI_ENABLED=false`、合成 Telegram 凭据、私有
   `DATABASE_URL`、`QUIET_START_HOUR=QUIET_END_HOUR=0` 等）。
2. 关闭 `Settings.model_config["env_file"]`，再显式构造
   `Settings(_env_file=None)`；因此项目 `.env` 无法提供数据库、AI 或通知凭据。
3. 导入项目模块，重新绑定到同一个绝对路径的私有 SQLite
   `data/soak/<run_id>/soak.db`，并断言 `app.db`、worker、pipeline 读到同一
   `settings()` 实例。创建 `data/soak/<run_id>/` 时要求该目录**不存在**；已存在
   目录、符号链接、`data/soak/` 之外的路径一律拒绝。
4. 只用 mock RSS 响应和本地 Notifier；通知 payload 只写
   `notifications.jsonl`，不发送到任何渠道。
5. 进程内 `NetworkGuard` 拦截并计数 `socket.getaddrinfo`、`socket.connect`、
   `socket.connect_ex`、`socket.sendto`、`socket.sendmsg`、`httpx.Client`、
   `httpx.AsyncClient`、`security.fetch`、`rss.fetch`、`gdelt.fetch`。只拦截
   `AF_INET`/`AF_INET6`，Unix socket 语义保留；任何真实网络尝试都会使本次运行
   无法通过完成断言。

合成数据固定为 5 个 RSS 源 + 1 个租约探针源。默认每 60 秒一批，每批每源
放 1 条新文章和 1 条上一批已出现的文章；标题含源编号与批号，避免不同事实
被事件链接规则合并。重复文章必须不增加 `articles` 行数。

## 启动

从仓库根目录运行（脚本按 cwd 读取 `config/rules.yaml`）：

```bash
env -u PYTHONPATH .venv/bin/python -m scripts.soak \
  --duration-seconds 86400 \
  --interval-seconds 60
```

默认 duration 为 86400 秒，最大 7 天；默认 interval 为 60 秒。两个参数都
必须是有限正数，且有上下限（duration 0.1–604800，interval 0.05–86400）。

指定运行目录时，必须是 `data/soak/` 下**尚不存在**的直接子目录：

```bash
env -u PYTHONPATH .venv/bin/python -m scripts.soak \
  --duration-seconds 5 --interval-seconds 0.5 \
  --run-dir data/soak/manual-short-001
```

短程只用于验证链路；不要把这种运行当成 24 小时完成。

## 停止

在运行目录放入 `stop` 文件即可，工具在下一 tick 或等待间隙正常退出：

```bash
touch data/soak/<run_id>/stop
```

退出后 `summary.json` 的 `status` 是 `stopped`，`error_code` 是
`STOP_REQUESTED`，**不会**被写成 `completed`。已经提交的数据保留，未完成的
积压会保留在私有 DB 中。

## 查看证据

```bash
# 当前状态（原子替换，运行中约每批更新一次）
python -m json.tool data/soak/<run_id>/checkpoint.json

# 最终结果与断言
python -m json.tool data/soak/<run_id>/summary.json

# 结构化事件日志（只含计数与合成数据，不含凭据）
tail -n 20 data/soak/<run_id>/soak.log

# 本地 mock 通知记录
tail -n 3 data/soak/<run_id>/notifications.jsonl

# 只读检查私有数据库
sqlite3 'file:data/soak/<run_id>/soak.db?mode=ro' \
  'select status, count(*) from jobs group by status;'
```

`checkpoint.json` / `summary.json` 都以 `run_id` 标识归属；写入使用目标目录内
排他随机临时文件、`fsync` 后 `os.replace`。已存在的非本 run 文件（不同
`run_id`）或符号链接会被拒绝，不会覆盖别人的 sidecar。

## 状态与完成断言

状态只有 `running`（检查点）、`stopped`、`completed`、`failed`。退出码：
`0` = `completed` 或 `stopped`；`1` = `failed`；`2` = 参数/目录/写入等拒绝启动或
无法发布结果。

`completed` 不是只看状态码，必须同时满足以下断言，否则工具写
`status=failed`，连续性失败用 `error_code=CONTINUITY_GAP`，其余验收失败用
`error_code=VERIFICATION_FAILED`，并在 `failed_checks` 列出具体失败项：

- 没有明显长暂停/时钟跳变，连续性检查通过（见下节）；
- 没有任何 `unexpected_errors`；没有 failed job、没有未解释 backlog；
- 至少出现一次重复文章，重复行增加数为 0，文章唯一集合与预期一致；
- 文章数 = 事件数 = 通知创建数；通知全部 `sent`，本地 mock 送达数一致；
- mock 429 被实际观察到并恢复；队列租约探针恢复为 `completed`；
- 关库重开后行数不变，`PRAGMA integrity_check=ok`；
- `network_attempts=0`，没有 `openai-compatible` 模型调用；
- 隔离字段为真：`.env` 被禁用、私有 DB、模块共享同一 Settings。

## 连续性检查（长暂停/挂起）

历史版本的 review 复现过：`duration=10s, interval=0.1s`，完成 6 批后
`SIGSTOP 10.2s` 再 `SIGCONT`，仅按 deadline 退出会写成 `completed`。现在每批
记录进展时间和两类 gap，并在退出主循环前、最终验收前和 summary 发布前检查
“最后进展到现在”的 gap，避免跨 deadline 的暂停漏检：

- `observed_seconds` 仍只取 `time.monotonic`，只表示进程实际运行时长；
  **monotonic 经过不等于连续采样**。
- `max_gap_seconds` / `final_gap_seconds`：monotonic 进展间隔。允许上限为
  `max(2s, 2 × interval + 2s)`；默认 60s 观察对应 122s，短程 0.1s 对应 2.2s。
- `max_wall_gap_seconds` / `final_wall_gap_seconds`：UTC 墙钟进展间隔，用于
  发现挂起或大时间跳变。
- `max_wall_drift_seconds` / `final_wall_drift_seconds`：墙钟经过时间与
  monotonic 经过时间之差；正常 sleep/wait 两者同步推进，drift 接近 0，
  机器挂起时 monotonic 不推进而墙钟推进。drift 上限为 5s。
- 任一超限会写入 `cadence_violations`（含 phase、reason、gap、wall gap、drift、
  allowed），状态改为 `failed`，错误码 `CONTINUITY_GAP`；不会在恢复后快速补跑
  掩盖，也不会把挂起后累计的 24h 当成连续观察。

回归：`tests/test_soak.py` 有一条真实 `SIGSTOP 3s` 且跨过 2s deadline 的测试，
断言最终为 `failed`/`CONTINUITY_GAP`；另有纯时间差测试模拟“monotonic 不动、
墙钟跳 30s”，证明 wall drift 会单独触发不连续。

本机还按 review 原参数手工复现了一次：`duration=10s, interval=0.1s`，完成 6 批后
`SIGSTOP 10.2s` 再 `SIGCONT`。修复后结果为 `status=failed`、
`error_code=CONTINUITY_GAP`、`batches=7`、`observed_seconds=12.887`、
`cadence_violations` 的 reason 为 `monotonic+wall_clock`（gap 10.745s）、
`full_soak_completed=false`；证据目录
`data/soak/manual-sigstop-review-4202a645e7/`（未提交）。

`full_soak_completed` 只有在 **status=completed + 真实 monotonic 时钟 +
请求 duration ≥ 86400 + 连续性通过** 时才为真。mock 时钟、短程、stopped 或
长暂停都不会冒充 24 小时完成；`completion_scope` 会标为
`requested_duration_only`、`mock_clock_run`、`stopped` 或 `failed`。

## 受控故障与三项恢复证据

这些都在私有合成 DB 内完成，日志/摘要中明确标注为注入：

1. **mock 429（第 2 批）**：`SyntheticFeed` 对 `soak-src-0` 抛一次
   `RemoteError("HTTP_429")`。真实 worker 失败路径把 job 置
   `retry_wait`、来源进入 backoff。工具只把这一个合成 job 的
   `scheduled_at` 改为当前时间（`simulated_schedule_advance=1`），随后重试
   成功并恢复来源。这不是真实第三方 429。
2. **队列租约（第 3 批）**：直接插入一条 `status=running`、`started_at` 早于
   `job_lease_seconds` 的 collect job（探针源返回空 feed）。下一次
   `worker.tick()` 的 `queue.recover()` 将其恢复为 `retry_wait`，同一 worker
   领取并完成；记录 `status_before=running`、`status_after=completed`、
   `attempts_after`。这是合成行注入，不是 OS 崩溃或进程重启。
3. **关库重开（第 4 批）**：`engine.dispose()` 后用同一私有 SQLite 路径新建
   engine/session，执行 `PRAGMA integrity_check`，比较重开前后
   articles/events/notifications/jobs 行数。它验证已提交记录的持久性，不是
   生产重启恢复。

预计故障写在 `expected_failures`，意外错误写在 `unexpected_errors`；两者不会
混在一起，也不会因为“是故障演练”就忽略失败。

## 检查点过期/异常退出（容器与原生区分）

工具本身没有 watchdog。`checkpoint.json` 只说明进程在某一批结束时自报的状态，
**`summary.json` 才是最终结论**。判断“是否还活着/是否异常”时，先区分运行方式。

### 容器内运行（当前第五段方式）

`checkpoint.json` 的 `pid` 是**容器 PID 命名空间内**的 PID，不能拿它到宿主或
其他命名空间执行 `kill -0`。本编排 shell 自身可能还在独立的 bwrap PID
命名空间里，连 `docker inspect` 给出的宿主 init PID 也可能不可见；因此：

- **禁止**用 `kill -0 <checkpoint.pid>` 的失败来判定容器内 soak 死亡；
- 一律用**精确容器身份**（完整 container ID 或精确 name，不用可能有歧义的前缀）
  的 Docker 状态，再结合检查点归属与新鲜度判断；
- `docker inspect` 只读，不会创建、启动或停止观察实例。

适用于当前目录的只读示例（容器名必须与当前 run 精确一致）：

```bash
docker inspect --format 'name={{.Name}} id={{.Id}} image={{.Image}} running={{.State.Running}} paused={{.State.Paused}} exit={{.State.ExitCode}} restarts={{.RestartCount}} oom={{.State.OOMKilled}} started={{.State.StartedAt}} args={{json .Config.Cmd}}' \
  aih-soak-429779a-6dd86de1
```

示例中的名称/run 目录只对应本次第五段实例；不要为了套用示例而启动新容器或新
run。下面所有检查都只读，不产生新的观察实例。

判读规则：

- `Running=false` 且 `Paused=false`：进程已退出；`ExitCode` 是退出证据，随后必须
  以 `summary.json` 为准。`ExitCode != 0` 按异常退出；`ExitCode=0` 但缺少
  `summary.json` 仍不能算完成。
- `Paused=true`：进程被暂停；暂停时间和恢复后的补跑都不能算作连续观察。
- 顶层 `RestartCount > 0`：容器发生过重启，不能当作“同一容器连续运行”，按异常处理。
- `OOMKilled=true`：按异常退出处理，并保留 Docker/内核证据。
- `Running=true` 只证明容器还在，不证明批处理仍在推进；必须继续核对检查点。

再只读核对检查点是否属于该 run、以及是否超过 `2 × interval` 未更新（当前 60 秒
interval 即 120 秒）：

```bash
RUN_ID=container-24h-429779a-6dd86de1
python3 - "$RUN_ID" <<'PY'
import json
import sys
from datetime import datetime, timezone

run_id = sys.argv[1]
path = f"data/soak/{run_id}/checkpoint.json"
c = json.load(open(path, encoding="utf-8"))
updated = datetime.fromisoformat(c["updated_at"])
age = (datetime.now(timezone.utc) - updated).total_seconds()
print("run_id_match=", c["run_id"] == run_id)
print("status=", c["status"], "batches=", c["batches"])
print("updated_at=", c["updated_at"], "age_seconds=", round(age, 1))
print("stale_over_2x_interval=", age > 2 * c["interval_seconds"])
PY
```

- `run_id_match=False`：目录/检查点归属不符，不要继续推断，先核对容器
  `.Config.Cmd` 里的 `--run-dir`。
- `stale_over_2x_interval=True` 且容器仍 `Running=true`：按“疑似停滞”报告，
  保留容器、目录和日志；不要用 `docker stop/restart`、`touch stop` 或重新
  `docker run` 来“修复”现场。
- 容器已退出时，检查点可能仍停在最后一份 `status=running`；这是旧快照，
  不能覆盖 `summary.json` 的结论。

### 原生非容器运行

只有检查进程与 soak 进程处于**同一 PID 命名空间**（原生直接运行，没有
容器/bwrap/其他 PID namespace 隔离）时，才可以用检查点 PID 做存在性探测：

```bash
# 仅限原生、同一 PID 命名空间；容器方式不要执行
kill -0 <checkpoint.json 中的 pid>
```

即使在同一命名空间，`kill -0` 也只证明 PID 存在，不证明批处理推进；仍须结合
`updated_at`、`soak.log` 和最终的 `summary.json`。

### 共同规则

- 查看 `soak.log` 最后事件与 `summary.json` 是否存在；只有 summary 才是最终
  结论，不能用最后一份 running 检查点声称完成。
- 不要删除、覆盖或复用该目录；保留现场，报告 `run_id`、容器 name/ID、
  `State.Running/Paused/ExitCode/OOMKilled`、顶层 `RestartCount`、PID 的作用域、
  最后 `updated_at`、`batches`、backlog 和短错误码。
- 检查点异常与“24 小时未到”是两件事：未到时长只能继续等待，不能提前判通过。

## 测试

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest tests/test_soak.py -q
env -u PYTHONPATH .venv/bin/ruff check scripts/soak.py tests/test_soak.py
env -u PYTHONPATH .venv/bin/ruff format --check scripts/soak.py tests/test_soak.py
```

测试覆盖：参数边界、目录/符号链接拒绝、原子写不覆盖别人的 sidecar、完成
断言、mock 时钟不冒充 24h、monotonic gap 与墙钟 gap/drift 连续性、真实
`SIGSTOP` 跨 deadline 回归、网络守卫拦截计数（含 `connect_ex`/`sendto`/
`sendmsg` 与 Unix socket 保留）、短程完整链路（含重复、429、租约、关库重开）、
过短运行拒绝 completed、stop 文件写 stopped。

## 本机短程实测（未提交 data/）

### 修复前历史短程（保留，不当作修复后证据）

该结果产生于连续性检查加入前，`summary` 不含 `continuity` 字段；保留为历史：

```bash
env -u PYTHONPATH .venv/bin/python -m scripts.soak \
  --duration-seconds 4 --interval-seconds 0.2
```

`data/soak/run-20261024T161416Z-74d14916/`：`status=completed`、
`observed_seconds=4.012`、`batches=15`、`articles=events=notifications_created=
notifications_sent=75`、重复 feed 条目 70 条而 `duplicate_rows_inserted=0`、
mock 429 已观察并恢复、租约 job `completed`、关库重开前后 25/25/25/76 行且
`integrity_check=ok`、`network_attempts=0`、backlog 为 0、
`full_soak_completed=false`。

### 修复后短程（连续性检查生效）

同一命令，本机新结果为 `data/soak/run-20261024T165716Z-15ca56e9/`：
`status=completed`、`observed_seconds=4.211`、`batches=16`、
`articles=events=notifications_created=notifications_sent=80`、重复 feed 条目
75 条而 `duplicate_rows_inserted=0`、mock 429 已观察并恢复、租约 job
`completed`、关库重开前后 25/25/25/76 行且 `integrity_check=ok`、
`network_attempts=0`、backlog 为 0、`full_soak_completed=false`。
连续性：`allowed_gap_seconds=2.4`、`max_gap_seconds=0.548`、
`max_wall_gap_seconds=0.548`、`max_wall_drift_seconds=0.0`、
`final_gap_seconds=0.01`、`cadence_violations=[]`、`ok=true`。

这里的 `completed` 只表示请求的 4 秒已完成，不能当作 24 小时完成。

## 限制

- 合成 RSS 与本地 Notifier 不验证真实 RSS 格式漂移、第三方限流、真实网络
  延迟、真实凭据或消息平台行为。
- 429 是 mock collector 抛出的错误；租约与关库是显式注入/主动重开。
- `observed_seconds` 是 monotonic，不是墙钟连续采样；挂起只靠墙钟 gap/drift
  保守发现。阈值内的短暂停仍可能通过，超过允许 gap 或 5s drift 会失败。
- 单 worker、低负载；不覆盖并发领取、双 worker、SQLite 写锁竞争或 controller
  重启。
- 未经 Codex 审查，不要自行启动 24 小时进程；短程通过不等于长程通过。
