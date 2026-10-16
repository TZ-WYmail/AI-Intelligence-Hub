# AI Intelligence Hub · AIH

个人可部署的多行业情报采集与分析平台。Python 3.12 / FastAPI / Streamlit / SQLite WAL。

已实现真实 RSS → 标准化 → 三层精确去重 → 持久化文章/任务 → 行业分类 → 规则评分 → 可选 AI 分析 → 持久化告警 → Web 看板。当前交付为可运行的 MVP 工程，**尚不宣称 SRS 全部验收完成**：GDELT 实网验证失败、真实 AI/通知凭据未提供，持续运行观察尚未完成。

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

此次开发已完成正式 Docker 部署，当前四服务由 Docker 管理；原本地开发进程已停止。正式数据在 `aih_aih-data` 卷，本地 `data/aih.db` 与迁移备份保留。

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
AI_TIMEOUT=30
AI_MAX_OUTPUT_TOKENS=1200
AI_DAILY_REQUEST_LIMIT=100
MONTHLY_AI_BUDGET_CNY=50
AI_INPUT_CNY_PER_MILLION=10
AI_OUTPUT_CNY_PER_MILLION=30
```

价格必须按实际供应商设置，不代表任何供应商当前报价。兼容 `/chat/completions`、JSON response format；需要支持该协议的模型。预算在调用前预留保守费用；失败请求亦保留预留费用，防止失败重试失控。实际有 Token 用量时按配置单价计费；没有 usage 时保留预留值。按 UTC 日/月统计。额度不足自动使用规则结果。首次规则分析先提交，AI 失败使用同一事件重试，不阻塞采集。

AI 分析将新闻文本包装为不可信 JSON 输入；模型无命令执行工具，结果通过 Pydantic 校验。无依据的 confirmed 会强制回到 unverified，事实只允许引用当前文章。AI 推测独立显示；**MVP 告警采用可解释规则评分，AI 自报高分不能单独升级告警**。未启用 AI 时不会调用模型。端口 3080 上的本地 DeepSeek 网页返回 401，`/v1/models` 返回 404；没有推断/复用其登录凭据，未将其当作可用模型 API。

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

更多资料：[验收与实测](docs/ACCEPTANCE.md)、[已知限制与下一阶段](docs/LIMITATIONS.md)、[故障排查](docs/OPERATIONS.md)、[真实来源验证](docs/source-validation.json)、[合成压测](docs/benchmark.json)。
