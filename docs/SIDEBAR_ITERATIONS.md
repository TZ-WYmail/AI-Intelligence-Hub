# 侧边栏迭代记录

本记录独立于既有十轮前端优化，不重新计数。每轮只改侧栏指定范围，完成后等待 Codex 审查；不自行标记最终验收。

约束：只改 dashboard/、相关测试和本文档；不读取或修改 `.env`、数据源配置、DSH 配置或后端/依赖；不启动服务/操作容器或用户 DB，不 commit/push。

## 第 1/4 轮：导航层次与导航命中区（Codex 已审查）

Codex 独立审查：独立运行侧栏/全页面/登录8项mock测试通过，Ruff通过。依据实际DOM返修label选择器；八个入口/单radio/未登录零API保留。整行44px与选中态待统一浏览器复核。

### 问题

- 品牌、密码输入和 8 个 radio 挤在一起，侧栏缺少稳定的顶部节奏。
- 导航只有默认小圆点和文字，当前页没有整行高亮，点击区偏小。
- 需要保留一个原生 `工作空间` radio、8 个中文选项及顺序，不能拆成两个 radio。

### 改动

- 新增 `dashboard/sidebar.py`：紧凑品牌头（◈ + AIH 情报中心 + 小号英文副标题）；管理员密钥输入和唯一的原生工作空间 radio 仍在同一模块，行为不变。
- 导航 radio 增加稳定 key `workspace_nav`，label、8 个选项和顺序完全保留；没有拆分成双 radio，也没有假导航。
- 侧栏 CSS 全部以 `[data-testid="stSidebar"]` 限定：`stRadioOption` 本身就是 label，直接在该元素上定义 flex、width 100%、min-height 2.75rem、padding、cursor；组内 0.2rem 间距、hover 底色、当前项青绿浅底/左侧强调条；选中态定向覆盖 `p` 的颜色/字重。使用 `data-selected` 并以 `:has(input:checked)` 兜底，不改其他页面/图表。
- 按 Codex 实际 DOM 复核修正：删除不存在的“stRadioOption 内部 label”选择器；行布局和选中态 `p` 的 color/weight 直接作用于 `stRadioOption`，原生 input/语义保留。
- 管理员密钥、未登录提示、8 页业务和底部“unverified”说明保留；未实现凭据收纳/退出、窄屏折叠、键盘焦点等后续轮次内容。

### 实际验证

- 新增 `tests/test_sidebar.py`：未登录时只有 1 个原生 sidebar radio、label 为“工作空间”、选项为原 8 个且不请求 API；登录后逐页选择 8 个页面均无异常且 radio 值正确。
- 前端 mock 组共 `27 passed, 1 warning in 7.94s`（警告为依赖的 Starlette/httpx 弃用提示）。原有断言未删减或放宽。
- Ruff `check` 与 `format --check` 通过，`82 files already formatted`。

### 局限

- 选中态、hover 和 44px 整行点击区仍需 Codex 在真实浏览器确认；本轮未做浏览器截图。
- 品牌/间距为第一轮范围；凭据收纳、退出、窄屏折叠和键盘焦点留待后续三轮。
- 未操作真实数据/容器/凭据，未 commit/push。
