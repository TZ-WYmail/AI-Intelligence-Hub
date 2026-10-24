# V1 交付验收报告

日期：2026-10-24（Asia/Shanghai）。主机 venv Python 3.12.14；本轮不启动生产服务、不发送真实通知、不调用项目配置的新闻分析 AI 接口。

本报告记录 V1 三段（失败任务恢复、事件关联准确性、部署与交付证据）的实际执行结果。结论：**P0 主链路与本轮三段验收通过**；但通知真实投递、GDELT 实网、24–72 小时稳定性等仍未验证，**不宣称完整 SRS 全部验收完成**。未完成项见 [LIMITATIONS.md](LIMITATIONS.md)。

## 执行方式

本轮实现由 DeepSeek Harness 会话实际完成并委派，Codex 负责独立审查、反例复现与实测。临时连接与凭据细节不写入文档。V1 三轮的过程记录见 [V1_ITERATIONS.md](V1_ITERATIONS.md)，事件关联修复细节见 [EVENT_LINKING_REVIEW.md](EVENT_LINKING_REVIEW.md)。

## 1. 全套自动化测试

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest -q
```

结果为 **110 passed, 1 warning，5.90 秒**。全套覆盖采集、去重、分类评分、API、看板页面、迁移备份、通知批次隔离、租约恢复、事件关联与失败任务重试。

唯一警告为 Starlette 提示 `TestClient` 的 httpx 后端未来迁移到 httpx2；属测试工具的上游弃用提示，不影响当前测试与运行。

## 2. 独立审查用例（临时文件，不在仓库）

Codex 另建 `/tmp/aih_independent_review.py` 独立复核，结果 **13 passed, 1 warning，0.43 秒**。覆盖：

- 6 组“标题相似但事实不同”的应拒绝关联用例；
- 3 组畸形文章标识（list/dict/bool，仓库内等价测试用 list/dict/int）；
- 倒置的日报时间窗口；
- 2 组坏 `channel` 参数（list/dict）；
- 并发删除任务后的重试返回。

该脚本是临时本地审查证据，**不属于仓库文件，也不是用户必须运行的步骤**；等价行为已由仓库内测试覆盖。

## 3. 静态检查

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

两项均通过，共检查 **67 个文件**。

## 4. 合成负载基准

```bash
env -u PYTHONPATH .venv/bin/python -m scripts.benchmark
```

实际 exit 0，结果写入 [benchmark.json](benchmark.json)：

| 指标 | 数值 |
| --- | ---: |
| 模拟来源 | 100 |
| 文章 | 3,000 |
| 事件 | 3,000 |
| 模拟通知投递 | 3,000（sent） |
| 未完成任务 | 0 |
| 失败任务 | 0 |
| 总耗时 | 46.682 秒 |
| 入库到规则完成 P95 | 0.4642 秒 |
| 通知排队到模拟接受 P95 | 0.0027 秒 |

运行使用单 Worker、临时 SQLite、mock 采集器与立即接受的 mock 发送，**没有真实第三方网络或模型延迟，不是生产 SLA**。

脚本现在强制校验文章、事件、通知数量与未完成/失败任务数，不符即 exit 1 且不写报告。此前一次修复前的运行只产生 2,668 条通知且无断言，属于可被误当成通过的场景；该缺口已由强制校验关闭。

## 5. Compose 配置与镜像构建

- `docker compose config --quiet` 通过。
- 使用现有代理构建参数实际构建成功：`docker build --network host`（代理参数不写入镜像），产出本地镜像 `aih:local`。构建日志为未跟踪的本地文件，未纳入交付内容。

构建成功只证明镜像可生成，**不等于生产 Compose 已启动验证**。

## 6. 隔离容器内加载检查

在临时隔离容器中运行（`--network none --read-only --tmpfs /tmp`），仅提供 mock 管理员密钥，AI 与通知关闭，数据库使用内存实现；实际完成：

- `import app.main`；
- SQLAlchemy 在内存库建表（**10 张表**）；
- 生成 OpenAPI（**28 条 paths**）；
- 容器 Python 为 3.12.12。

该检查证明镜像内代码可导入且 Schema 完整，**不是 Compose 生产启动、四服务健康或数据卷验证**。

## 7. 镜像内容一致性

镜像内 `app/services/queue.py`、`dashboard/Home.py`、`app/processing/event_matcher.py` 三个关键文件的 SHA256 与工作区一致，确认构建的是修复后代码。

原生产 Compose 的 api、dashboard、worker、scheduler 四个容器均保持 exited，本轮未启动；数据卷与 `.env` 原样保留。

## 8. Git 同步状态

GitHub SSH 已验证可用。本报告涉及的前三个代码提交 `4a354a1`（通知批次与租约恢复修复）、`93456c0`（事件关联修复）、`9d5701a`（任务恢复）均已推送，远程 `main` 现指向 `9d5701a`。文档提交待 Codex 最终同步，因此本报告不将文档同步视为已完成。

## 9. 未覆盖与限制

- 通知遵循用户要求**未接入真实发送**；本轮只用 mock 渠道。
- AI 的真实单篇分析有 [STAGE2.md](STAGE2.md) 的历史证据；**本轮未对项目配置的新闻分析 AI 接口发起新的实网调用**，基准与页面测试均为 mock。
- 来源验证为旧日期快照（22 个启用订阅，见 [source-validation.json](source-validation.json)），本轮未重新验证，**不代表这些来源当前全部在线**。
- GDELT 仍为实网禁用；24–72 小时稳定性观察、Ubuntu 24.04 与生产网络 P95 未验证。
- P1/P2 条款仅部分实现，研究型日报/周报、语义去重、事件版本管理等未完成。
- 多 Worker、PostgreSQL 迁移、真实供应商账单对账未验证。

因此，本报告可支持“V1 交付范围内的主链路与三段修复可复现地通过自动化验收”，不能支持“完整 SRS 全部验收完成”。

## 复现命令

在仓库根目录执行；宿主的 `PYTHONPATH`/pytest 自动插件会干扰，故用 `env -u` 清理：

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
env -u PYTHONPATH .venv/bin/python -m scripts.benchmark   # 会重写 docs/benchmark.json
docker compose config --quiet
```

基准脚本会在临时目录建库并在结束时销毁，不读取或写入真实 `data/aih.db`；但它会覆盖 `docs/benchmark.json`，如只需查看结果请直接读取该文件。

## 相关文档

- [首版验收记录](ACCEPTANCE.md)（2026-10-16，历史）
- [已知限制与后续工作](LIMITATIONS.md)
- [V1 迭代与问题修复记录](V1_ITERATIONS.md)
- [事件关联修复记录](EVENT_LINKING_REVIEW.md)
- [初代产品分段验收目标](V1_ROADMAP.md)
- [SRS 实现对照](SRS_TRACEABILITY.md)
