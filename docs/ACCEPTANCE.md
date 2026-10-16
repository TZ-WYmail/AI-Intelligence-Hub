# MVP 实测与验收记录

日期：2026-10-16（Asia/Shanghai）。宿主 Ubuntu 26.04.1，Python 3.12.14，Git 2.53.0，Docker 29.1.3，Compose 2.40.3。Ubuntu 24.04 尚未单独测试。

## 已执行且通过

- 26 项 pytest 测试，耗时约 2.2 秒（实际耗时随运行波动）。包含 RSS/Atom 字段解析、损坏输入、缺时间、中英文、HTML 清理、时间/URL/内容哈希、关键词边界/排除、评分、配置/Schema 校验。
- 模拟端到端：RSS → SQLite → 去重 → 行业 → 规则事件 → 持久化通知 → 模拟渠道确认。再次采集不重复入库，再次发送已成功记录不重复调用。
- 兼容 AI 模拟请求验证：不可信新闻放在独立 JSON 中、无命令执行能力、非法 JSON 拒绝、confirmed 降回 unverified、不存在来源引用丢弃、失败仍保留规则事件、额度耗尽不调用模型。
- Telegram 与飞书分别测试模拟 200、429、500；P1 两条合并、静默延迟。以上不是实际第三方发送。
- 任务 CAS 领取、重复调度去重、running 租约超时恢复、网络 429/timeout 后重试、坏来源不阻断好来源、数据库错误后恢复；重新打开数据库仍可读取已提交文章。
- 安全：未授权业务访问 401、非法参数 422、私网/环回/元数据地址拒绝、DNS 固定/TLS SNI、重定向重新验地址、响应超限拒绝、时间参数必须带时区。
- FastAPI CRUD、搜索/统计/OpenAPI；Streamlit AppTest 运行登录与八个页面无异常。实际浏览器另确认登录页可打开，实际 HTTP 看板健康探针返回 200。
- Alembic 空库升级 → 初始化 → downgrade base → upgrade head；数据库完整性检查 ok。在线备份可重新打开、原文件存在时拒绝覆盖。
- Ruff 检查与格式检查通过。

测试指令：

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

已知一个上游警告：Starlette 提示其 TestClient 的 httpx 后端未来将迁移到 httpx2；当前测试通过，尚不影响运行。

## 真实外部信息源

22 个实际候选中，20 个能获取并解析非空订阅，2 个失败保持禁用。20 个来源属于 NVIDIA、NASA、EIA、ECB、arXiv 五个平台，跨多个订阅分类。完整时间戳、条目数量、最近来源时间、公开订阅证据和使用限制见 source-validation.json、SOURCE_POLICY.md。

本地后台四服务启动后，20 个启用来源全部成功采集。2026-10-16 约 17:02 快照：**1,171 条文章、1,171 个单文章事件、1,191 个已完成任务，无任务积压**。这是实际数据库/API 查询结果；数量未来会变化。一篇文章对应一个事件属于 MVP 边界，不表示跨源语义聚合完成。

GDELT：协议适配和 fixture 测试通过，但真实请求失败（首次 RemoteError，随后复核触发 TOTAL_TIMEOUT）；保留禁用。NVIDIA Blog：ConnectTimeout，禁用。未为 Anthropic、FDA、中国政府网等虚构 RSS。

本地 DeepSeek：主页 HTTP 401，尝试的 `/v1/models` HTTP 404；未取得可用模型 API/登录，不用于委派或作为可用 AI 服务声明。

## 合成负载

实际运行 `uv run python -m scripts.benchmark`：

- 100 个模拟来源，每源 30 条，总 3,000 条唯一文章。
- 完成 3,000 次模拟通知投递，失败任务 0。
- 总计约 18.009 秒；入库到规则完成 P95 0.1495 秒；通知排队到模拟接受 P95 0.0023 秒。
- 单 Worker、临时 SQLite WAL、无真实网络/AI、模拟通知立即接受。原始机器可读数据见 benchmark.json。

这些数字仅证明合成任务链路吞吐与持久化逻辑，不能作为真实第三方延迟保证，也不替代 24–72 小时稳定性观察。

## Docker / 数据保留

Docker Compose 配置验证通过。初次 Docker Hub 基础镜像解析超时后改用 Astral 官方 GHCR Python 3.12 镜像；首次依赖下载超时后增加 UV_HTTP_TIMEOUT 与构建缓存，镜像实际构建成功。四服务/重启结果在本节后追加。

本地真实数据库另完成在线备份：1,171 条文章，备份数据库 `PRAGMA integrity_check` 为 ok。真实凭据、.env、数据库、日志和备份均通过 .gitignore/.dockerignore 排除，不提交 GitHub。

## 尚未完成的验收

- 未提供真实 AI、Telegram、飞书凭据，尚不能确认真实模型结果与真实通知到达。
- GDELT 真实接入尚未通过。
- 未跑 24–72 小时持续观察，未测 Ubuntu24.04，未验证生产网络 P95 延迟。
- P1/P2 与其他未完成条款详见 LIMITATIONS.md / SRS_TRACEABILITY.md；因此未将完整 SRS MVP 最终验收全部打勾。

### Docker 追加实测

- 基于 Python 3.12.12 的实际镜像成功构建；Compose 的 init 迁移/初始化退出码 0。
- 独立端口验收中 api、worker、scheduler、dashboard 四服务均为 healthy；API 业务查询与看板健康探针均返回 200。
- 实际重启上述四服务后，已提交文章保留：186 → 219 条；期间采集继续，数量增加。详细前后快照见 docker-validation.json。
- 验收容器已停止（其数据卷保留）。正式部署采用 Compose 项目 `aih`，将本地真实数据在线备份迁入 `aih_aih-data` 命名卷；保留原 data/aih.db 和独立备份。正式入口继续为 localhost:8000 和 localhost:8501。
- 当前运行模式为 Docker，带 unless-stopped 重启策略。不要同时启动本地开发四服务占用相同端口。
