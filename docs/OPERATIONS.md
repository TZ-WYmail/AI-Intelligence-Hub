# 故障排查与运维

- 页面无数据：先检查管理员密钥，再检查来源启用状态、调度心跳、Worker 和任务日志。无 AI 凭据仍应有规则事件。
- 401：输入本机 `.env` 的 `ADMIN_API_KEY`；不要把密钥放 URL 或浏览器书签。
- 来源错误：来源管理查看 last_error/checked_count，点击检测；ConnectTimeout/HTTP_403/429 不意味着接口地址无效，也不意味着可以绕过限制。失败指数退避，达到次数上限后本轮 failed；下一轮采集在 next_poll_at 后可重新投递。
- GDELT：当前配置禁用；来源检测成功且条款确认后再启用，至少保持 15 分钟间隔，查询语法见 GDELT 官方 DOC 2.0 文档。
- AI 错误：先看 ai_analyses 的 failed 状态、用量与任务错误类别；检查 Base URL 是否以 /v1 为结尾、模型是否支持 JSON response format。失败时规则文章/事件已经提交。达到最大次数的 failed 工作需排除故障后人工重新投递或重新处理；未提供自动无限重试。
- 通知 pending：先看渠道是否启用和 Worker 是否运行；P1 等待批次/静默结束。failed 查看 HTTP 类别；凭据不得贴入问题反馈或日志。系统设置的测试按钮只代表进入队列，必须看到 sent 才是接口接受。
- 重启恢复：pending/retry_wait 仍保留，running 超过租约（默认 300 秒）才恢复，未到租约时短暂等待是正常的。退出时不能清空数据库。外部通知有 at-least-once 的极端重复窗口。
- SQLite 锁：确认不是意外开了多个 Worker，检查磁盘权限/空间和 WAL；不要通过无限重试解决持续写锁。生产容量增大时迁移 PostgreSQL，并替换 json_each 查询/全局预算计数。
- Python/pytest 遇 ROS 插件错误：`env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q`；不要删除宿主 ROS 环境。
- Streamlit 首次要求 email 后退出：使用文档中的 `--server.headless=true`；开发启动脚本已包含此参数。
- Docker 镜像/包下载失败：检查 DNS/可信代理；`compose.build-local.yaml` 仅改变构建网络，不改变服务端口和采集器安全策略。可执行 `docker compose -f compose.yaml -f compose.build-local.yaml build --build-arg HTTP_PROXY --build-arg HTTPS_PROXY`，随后 `docker compose up -d --no-build`。
- 本地服务日志在 data/，控制台工作日志为 JSON（错误只记类别，不记录凭据/完整异常 URL）；API access log 默认关闭，避免查询泄露。Compose 使用 logging max-size/max-file 轮转；生产保留可另配日志收集。
- 原始证据保留：raw_payload 是原始 RSS 条目的未清洗解析字段（保留 HTML），标准 summary 清理 HTML；不保存整个 HTTP 响应字节或原文全文。30 天后清空 raw_payload，元数据长期保留。
- 调整保留参数后需等待每日清理；通知仅清理已成功发送的旧记录，待发/失败记录保留用于追查。
- 备份恢复：README 提供 SQLite 在线备份命令；定期复制到机器外的受控位置，定期在独立数据目录验证恢复。请勿将真实数据库提交 GitHub。

- 自动备份：默认24小时一份，保留7份，位于Docker数据卷的data/backups。查看jobs中的backup任务和backup_success指标；在线备份经过完整性检查，文件权限0600。配置关闭仅停止新任务投递；已经排队的任务仍会完成。备份需另行复制出本机。
- 每日报告：默认上海时间08:00生成前一天报告；手动“生成昨日日报”也进入队列，稍后刷新归档。任务类型daily_digest，不发送消息、不新增模型调用；归档按时间范围唯一。服务离线多日时仅自动补最近一日，较早日期可用自定义报告汇总。
- 阅读清单：已读/收藏写入articles表，备份会包含这些状态；字号/宽度是当前浏览器会话偏好。连续阅读范围为当前列表一页，返回列表后可继续翻页。打开未读清单中的文章会自动已读，返回时回到剩余未读清单第一页，避免偏移跳过文章。
