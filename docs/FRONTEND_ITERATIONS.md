# 前端迭代记录

本文档按轮记录前端分析、改动、实际验证和仍未解决的限制。只有 Codex 明确给出审查结论后，才会记录为已验收；本文件不自行标记验收状态。

约束基线：不操作既有容器/服务，不读取 `.env` 或凭据，不调用外部新闻/AI/通知，不读写用户数据库或共享 `data` 状态；测试只使用临时数据库和 mock。每轮只处理指定范围，不 commit/push。

---

## 第 1 轮：统一视觉基础（Codex 已审查）

Codex 独立验证：页面与阅读回归 7 项通过；新增登录行为测试 1 项通过；改动文件的 Ruff 与格式检查通过。源码审查确认认证、HTML 转义及既有导航保留。浏览器视觉核查留在十轮完成后的统一部署阶段。

### 问题

- 共享视觉只存在于 `dashboard/Home.py` 的几条内联 CSS 中，颜色、字号、卡片和链接样式散落；`news.py` 又单独定义了阅读页样式，页面之间缺少明确的 token 层。
- 原内联样式把全局 `a` 设为青绿色并带 `!important`，选择器过宽，容易波及图表/表格等非导航链接。
- 默认页面顶部留白和一级标题偏大，首页头部显得过高。
- 登录页只有一句 `st.info`，没有说明密钥从哪里来、输入后会发生什么，也没有说明密钥不会被回显或自动填充。

### 改动

- 新增 `dashboard/theme.py`：
  - 集中定义深色背景、面板/表面、分隔线、正文/弱化文字、青绿色强调色、焦点色和字体栈。
  - 提供 `stylesheet()`、`apply_theme()` 与 `card_key()`；样式只使用现有稳定选择器：`.stApp`、`[data-testid="stSidebar"]`、`[data-testid="stMetric"]`、`[data-testid="stMainBlockContainer"]`、`[data-testid="stMarkdown"]`、`[data-testid="stForm"]` 以及按钮容器。
  - 收敛字体层级：压缩 `h1` 尺寸和上边距，统一 `h2/h3`、正文行高、说明文字和 Markdown 链接颜色。
  - 统一指标卡、表单、带 `aih-card-` key 的卡片、按钮、链接、分隔线和可见焦点样式。
  - 窄屏媒体查询只压缩页面/卡片内边距和标题字号，不隐藏任何控件或功能。
- `dashboard/Home.py`：
  - 删除原有内联 CSS，改为在 `set_page_config` 后调用 `theme.apply_theme()`。
  - 登录页改为“眉标 + 标题 + 说明 + 两张卡片”：明确说明从本机 `.env` 输入 `ADMIN_API_KEY`、输入后的行为、窄屏展开侧栏方式，以及密钥不回显/不自动填充/不写入文件的边界。
  - 管理员密钥输入框保持 `type="password"`、默认空值，并显式设置 `autocomplete="off"`；不新增预填值或密钥展示。
  - 概览“最新情报”卡片使用共享 `card_key()` 接入统一卡片样式。
  - 导航 radio 的位置、选项和顺序未改；既有主要按钮标签未改。
- 未改动 `news.py` 的阅读页局部样式；共享主题只提供全局背景、字体、按钮和链接基础，阅读页仍保留自己的标题、摘要、事实框和阅读宽度规则。
- 选择器刻意避开 Plotly、DataFrame 和组件内部 DOM，不覆盖图表配色、表格内部样式或 BaseWeb/React Aria 私有结构。
- 新增 `tests/test_theme.py` 中的一个 AppTest 回归：未登录时不请求 API、密钥输入为空且为密码类型、`autocomplete=off`、登录说明包含进入方式和“已选择的工作空间（默认概览）”、导航顺序不变。

### 实际验证

- 测试命令（运行前显式设置环境变量）：

  ```bash
  DATABASE_URL=sqlite:///:memory: AI_ENABLED=false TELEGRAM_ENABLED=false FEISHU_ENABLED=false \
    env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    .venv/bin/pytest -q tests/test_theme.py tests/test_dashboard.py tests/test_news_reading.py \
    tests/test_reader_workspace.py tests/test_visual_reports.py
  ```

  结果：`16 passed, 1 warning in 3.41s`。警告为依赖自带的 Starlette/httpx 弃用提示，与本轮改动无关。覆盖登录/全部工作空间页面、网关错误、运行日志重试、新闻列表与阅读页、简报分页和新增的登录回归。

- Ruff：

  ```bash
  .venv/bin/ruff check .
  .venv/bin/ruff format --check .
  ```

  结果：`All checks passed!`；`78 files already formatted`。

- AppTest 实测：空密钥时只渲染登录页，管理员密钥输入 `label=管理员密钥`、`value=""`、`type=1`（密码）、`autocomplete=off`；导航选项仍为概览/实时新闻/重大事件/行业管理/数据源管理/简报/系统设置/运行日志；登录页不调用 `httpx.request`。
- 未启动 Streamlit/API/容器，未读取 `.env`，未调用任何外部服务或真实模型/通知；测试使用 AppTest mock 与临时 SQLite。

### 限制

- 本轮只建立共享主题并接入 `Home.py`；`news.py` 的阅读页局部样式、简报页和图表内部配色未重新设计，后续轮按需对齐。
- 输入框/下拉框等复杂控件只补了可见焦点状态，未覆盖其内部背景与边框。Codex 已明确本轮不依赖 BaseWeb/React Aria 私有 DOM，留待后续轮用更稳妥的方式处理。
- 卡片统一样式通过 `aih-card-` key 接入；尚未给所有页面卡片加 key，未加 key 的 `border=True` 容器只共享边框色和圆角。
- 未在真实浏览器中截图核对；本轮视觉结论基于 CSS/AppTest 结构验证，最终观感以 Codex 审查为准。
