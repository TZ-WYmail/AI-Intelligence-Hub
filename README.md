# AI Intelligence Hub · AIH

个人可部署的多行业情报采集与分析平台。Python 3.12 / FastAPI / Streamlit / SQLite WAL。

已实现真实 RSS → 标准化 → 三层精确去重 → 持久化文章/任务 → 行业分类 → 规则评分 → 可选 AI 分析 → 持久化告警 → Web 看板。当前交付为可运行的 MVP 工程，**尚不宣称 SRS 全部验收完成**：GDELT 在 2026-10-16 实网验证失败并保持禁用，2026-10-24 单次受控复核返回 50 条但未启用；真实通知尚未验证，持续运行观察尚未完成；DeepSeek 已接通。

## 可视化与简报阅读更新（2026-10-17）

首页新增可旋转的3D地球热区、行业分布、发现趋势、重要性、行业时间热力图、来源与语言分析。热区依据新闻地名提及或来源登记地区，未知值独立展示，不推断精确事件地点。归档及自定义简报支持分页与完整下载。详见 [功能与口径说明](docs/VISUAL_DASHBOARD.md)。

## 事件关联与来源质量更新（2026-10-17）

新增跨来源标题一致性关联和报道时间线，保持未核实状态；来源页区分采集成功与内容新鲜度，并显示缺失/未来日期。新增两个实测可用 FDA 订阅，累计22个启用来源（2026-10-17 历史快照；当前配置未改，最新复核见下）。详见 [第四阶段说明](docs/STAGE4.md)。

## 阅读与维护更新（2026-10-17）

新闻现支持独立阅读页、上一篇/下一篇、字号/宽度、已读和收藏清单。每日08:00归档前一天情报，复用已有摘要而不新增模型费用；每24小时自动备份数据库并保留最近7份。通知仍关闭。详见 [第三阶段说明](docs/STAGE3.md)。

## 当前进展（2026-10-17）

第二阶段已接通 DeepSeek 官方 API，支持非思考模式；新闻列表支持 AI 中文摘要、摘要/影响搜索和按需详情。新增历史文章单篇分析队列入口。通知依用户要求保持关闭。当前真实 AI 的初始配额为每日20次、月估算20元，历史整库不会自动重跑。详见 [第二阶段交付记录](docs/STAGE2.md)。此前首版验收记录保留历史日期，其“尚未接通真实AI”状态已由本阶段更新。

## V1 交付与任务恢复（2026-10-24）

V1 三段（失败任务恢复、事件关联准确性、部署交付证据）已完成并通过自动化验收：全套 110 项测试、Ruff 检查、合成基准、Compose 配置检查与镜像构建均通过。完整证据、复现命令和未覆盖项见 [V1 交付验收报告](docs/V1_ACCEPTANCE.md)。

“运行日志”页现在可以恢复失败任务：把“状态筛选”切到“失败”，勾选要重试的任务，点击“重试所选失败任务”。可重试的是**采集、分析、日报归档、备份**四类；**通知与通知汇总任务禁止手动重试**。重试会把原失败任务重新入队为 `pending` 并返回结果（同一任务行、同一 ID，不新建任务，保留审计记录），**不代表已经执行**——当前 Worker 未运行，需启动 Worker 后才会实际执行。重复点击或已有同类活动任务会去重，不会重复入队；原尝试次数与最近错误会保留，便于判断修复是否生效。批量选择时逐条提交，并分别汇总“已入队 / 已去重 / 失败（含任务 ID 与原因）”。API 入口为 `POST /api/v1/jobs/{id}/retry`。

## 来源受控复核（2026-10-24）

第四段对 `config/sources.yaml` 已登记来源做了一次只读快照复核：26 个来源中 19 个解析出条目、4 个合法空列表、3 个失败；原 22 个启用配置未改，仍不把历史数量当作当前可用数。本轮不启动生产，不修改启用状态，不新增或猜测 URL。

安全运行方式（串行、每源一次采集尝试、不自动重试）：

```bash
# 默认写入 docs/source-recheck-<UTC日期>.json，不触碰 config/sources.yaml
uv run python -m scripts.recheck_sources
# 仅在明确要替换已有报告时使用 --force；未加 --force 时运行中出现的输出会被拒绝，
# 输出路径为符号链接时始终拒绝
uv run python -m scripts.recheck_sources --force
```

结果入口：[复核说明与边界](docs/SOURCE_RECHECK.md)、[原始 JSON](docs/source-recheck-2026-10-24.json)。退出码约定：0=全部解析；**1=存在部分 empty 或 failed**（不是崩溃）；2=无任何解析；3=配置/输出/完整性错误。脚本拒绝写入 `config/sources.yaml` 与 `docs/source-validation.json`，不启用生产来源，不保存正文或原始响应。

## 快速开始

需要 Python 3.12 和 uv；Docker 模式不要求宿主安装 Python。所有命令在仓库根目录执行。

```bash
uv python install 3.12
uv sync --frozen
cp .env.example .env
# 在 .env 填入随机 ADMIN_API_KEY（至少 32 个随机字符），不要提交该文件
uv run python -c 'import secrets; print(secrets.token_urlsafe(32))'
uv run alembic upgrade head
uv run python -m scripts.bootstrap
uv run python -m scripts.local up
uv run python -m scripts.local status
# 停止本地服务
uv run python -m scripts.local down
```

此前已完成正式 Docker 部署，四服务由 Docker 管理；当前交付验收期间服务保持停止，原数据卷与 `.env` 保留。正式数据在 `aih_aih-data` 卷，本地 `data/aih.db` 与迁移备份保留。

此次开发环境已生成本地 `.env`（0600 权限）和随机管理员密钥；请直接查看该文件，不要覆盖它。打开 http://127.0.0.1:8501，在侧栏输入 `ADMIN_API_KEY`。窄窗口点击左上角展开侧栏。API 文档：http://127.0.0.1:8000/docs；公开探针：`/api/v1/health/live`、`/api/v1/health/ready`。业务接口统一要求 `X-API-Key` 请求头。密钥为空时拒绝访问全部业务数据。OpenAPI 文档只公开接口结构，不公开数据/密钥。

也可分别在四个终端启动：

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
uv run python -m app.workers.run
uv run python -m app.scheduler.run
uv run streamlit run dashboard/Home.py --server.address=127.0.0.1 --server.headless=true --browser.gatherUsageStats=false
```

## Docker 部署

```bash
# .env 必须已存在且 ADMIN_API_KEY 已配置
# 本地开发服务与容器不能同时占用 8000/8501
uv run python -m scripts.local down
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 worker scheduler
# 停止但保留数据库卷
docker compose down
```

`init` 先迁移并导入初始配置，再启动 api、worker、scheduler、dashboard。SQLite 持久化在 `aih-data` 命名卷；默认仅映射 localhost。初始镜像使用 Astral 官方 GHCR Python 3.12 镜像，依赖由 `uv.lock` 锁定。服务退出自动重启。**保持单 Worker / 单 Scheduler 实例**，以保证进程内域名限流和 AI 预算预留语义。公网部署另行配置 HTTPS、身份网关、防火墙，不可直接修改端口为公网后裸用。

如果本机 Docker Hub DNS/镜像拉取超时，可使用本机可信代理进行构建（不将代理写入镜像）：

```bash
docker compose build --build-arg HTTP_PROXY --build-arg HTTPS_PROXY
```

Linux 宿主 127.0.0.1 代理需要构建网络为 host；本项目同时提供 `compose.build-local.yaml`，仅用于该环境的构建选项。采集器有意忽略环境代理，防止来源 URL 借代理绕过地址校验；需确保容器能够直连来源。

## 配置与使用

- **行业管理**：页面可新建、修改、删除行业；编辑中英文关键词、排除词、企业/人物、告警阈值和渠道。短英文词使用词边界，排除词优先。`config/industries.yaml` 仅用于首次导入，后续数据库修改不会被启动脚本覆盖。
- **来源管理**：输入实际公开 RSS/Atom URL 或 GDELT JSON 查询 URL，选择行业和周期；点击“检测可访问性”，确认用途后启用；可立即采集、停用、归档、JSON 导入/导出。删除来源为软删除，以保留历史来源证据。新增 URL 会检查 DNS、公网地址、端口和协议，不接受凭据 URL。
- **来源配置文件**：`config/sources.yaml` 保存初始来源及验证元数据。`uv run python -m scripts.bootstrap` 只添加缺失来源；同名已存在配置通过界面/API 更新。重新验证：先 `uv run python -m scripts.discover_sources` 重定向到 `docs/source-discovery.jsonl`，再 `uv run python -m scripts.verify_sources`。验证会改写初始清单，需审查差异。
- **实时新闻**：全文关键词搜索（SQLite LIKE，非专业搜索引擎）、行业、来源、语言、发布时间范围过滤，三种排序和分页。详情显示原文、原始负载、规则与 AI 分析。时间按 `APP_TIMEZONE` 展示，数据库统一 UTC。
- **事件**：MVP 每篇独立文章建立一个事件；多媒体同事件聚合/语义去重属于后续工作。精确重复文章不新增事件，不重复通知。
- **简报**：自定义时间范围的 Markdown 来源列表及规则摘要；统一 `/api/v1/reports` 接口已经实现。自动 AI 日报/周报属于 P1。
- **系统设置**：页面仅安全展示参数；密钥、预算、保留周期通过服务器 `.env` 修改，重启服务后生效。普通前端不会自动获得服务器密钥。

### AI 配置

```dotenv
AI_ENABLED=true
AI_API_BASE_URL=https://your-provider.example/v1
AI_API_KEY=your-secret
AI_MODEL=your-model
# DeepSeek 使用 disabled；其他供应商保留 provider_default
AI_THINKING_MODE=provider_default
AI_TIMEOUT=30
AI_MAX_OUTPUT_TOKENS=1200
AI_DAILY_REQUEST_LIMIT=100
MONTHLY_AI_BUDGET_CNY=50
AI_INPUT_CNY_PER_MILLION=10
AI_OUTPUT_CNY_PER_MILLION=30
```

价格必须按实际供应商设置，不代表任何供应商当前报价。兼容 `/chat/completions`、JSON response format；需要支持该协议的模型。预算在调用前预留保守费用；失败请求亦保留预留费用，防止失败重试失控。实际有 Token 用量时按配置单价计费；没有 usage 时保留预留值。按 UTC 日/月统计。额度不足自动使用规则结果。首次规则分析先提交，AI 失败使用同一事件重试，不阻塞采集。

AI 分析将新闻文本包装为不可信 JSON 输入；模型无命令执行工具，结果通过 Pydantic 校验。无依据的 confirmed 会强制回到 unverified，事实只允许引用当前文章。AI 推测独立显示；**MVP 告警采用可解释规则评分，AI 自报高分不能单独升级告警**。未启用 AI 时不会调用模型。不要把第三方网页的登录凭据当作模型 API；兼容接口需单独配置，本项目未复用任何网页登录凭据。

### Telegram / 飞书

```dotenv
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
FEISHU_ENABLED=true
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-id
```

凭据配置后重启，在“系统设置”点击渠道测试。测试先入持久化队列，需 Worker 运行；在通知记录里确认 sent。飞书当前适配普通自定义机器人文本消息；签名机器人需另加签名适配，关键词安全策略需包含测试/情报内容。Telegram chat 必须允许 bot 发送。没有真实凭据时仅能证明模拟接口可用。

P0 默认 ≥85，优先发送；P1 默认 ≥60，按 `P1_BATCH_MINUTES` 合并（每批至多 5 条分块），静默时延迟；P2 仅归档。P0 不受静默时段影响。行业可自定义重大阈值及渠道。始终携带 unverified 标识，告警代表值得核实，不代表事实确认。每条发送记录含事件/版本/渠道/收件人/类型唯一键；失败不会标记成功，429 尊重 Retry-After，并有限重试。外部服务接受消息后、状态落盘前发生崩溃仍可能重复发送：第三方无幂等键时无法保证严格 exactly-once。

## 工程结构

完整文件列表见 [docs/PROJECT_TREE.txt](docs/PROJECT_TREE.txt)。

```text
app/
  main.py             FastAPI 路由、鉴权、Schema、分页与统计
  models.py / db.py   ORM、UTC 类型、SQLite WAL/外键
  core/               配置、结构化日志、URL/DNS/大小限制
  collectors/         Collector Protocol、RSS/Atom、GDELT
  processing/         清洗、URL/哈希、关键词分类、配置化评分
  ai/                 Provider Protocol、OpenAI 兼容适配器
  notifications/      Notifier Protocol、Telegram/飞书
  services/           持久化队列、采集/分析/告警事务
  workers/            任务领取、重试、租约恢复、消息发送
  scheduler/          APScheduler、定期投递、保留清理
dashboard/Home.py     八个管理/阅读页面
config/               行业、来源、评分规则
migrations/           Alembic 版本化 DDL
scripts/              初始化、来源发现验证、备份、压测、本地启动
tests/                单元、集成、异常、安全、页面运行测试
Dockerfile / compose.yaml / uv.lock / .env.example
```

核心接口：Collector 返回标准 `Collection`；Provider 返回经过 Schema 验证的分析及用量；Notifier 仅在第三方确认成功后返回。扩展新来源或渠道时实现相应 Protocol，并在 pipeline/worker 注册。关键业务配置保持在 YAML/环境变量，行业不写死在代码中。

## 测试与运维

```bash
# 此宿主带 ROS 的 PYTHONPATH/pytest 插件；干净环境直接 uv run pytest 即可
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run python -m scripts.benchmark
uv run python -m scripts.backup /secure/location/aih-backup.db
```

备份使用 SQLite backup API，包含 WAL 已提交内容，拒绝覆盖现有备份。恢复前停止全部服务，将备份复制到数据文件路径，确认属主/权限并移除旧数据库同名 `-wal`、`-shm` 临时文件后启动；先另行保留完整旧数据库及其伴随文件。Docker 备份示例：`docker compose exec worker python -m scripts.backup data/backup.db`，再使用 `docker compose cp worker:/srv/aih/data/backup.db ./backup.db` 导出。恢复可将备份复制回已停止的卷挂载位置；先备份原卷。

更多资料：[V1 交付验收报告](docs/V1_ACCEPTANCE.md)（2026-10-24，最新）、[首版验收记录](docs/ACCEPTANCE.md)（历史）、[已知限制与下一阶段](docs/LIMITATIONS.md)、[故障排查](docs/OPERATIONS.md)、[真实来源验证](docs/source-validation.json)、[合成压测](docs/benchmark.json)。
