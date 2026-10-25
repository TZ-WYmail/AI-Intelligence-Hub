import html
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import streamlit as st
import theme

st.set_page_config(page_title="AI Intelligence Hub", page_icon="◈", layout="wide")
theme.apply_theme()

st.sidebar.title("◈ AIH 情报中心")
st.sidebar.caption("AI INTELLIGENCE HUB · 个人情报工作台")
key = st.sidebar.text_input(
    "管理员密钥",
    type="password",
    value="",
    autocomplete="off",
    placeholder="输入后按回车",
)
base = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
# No key auto-filled or sent to browser. Enter explicitly per session.
page = st.sidebar.radio(
    "工作空间", ["概览", "实时新闻", "重大事件", "行业管理", "数据源管理", "简报", "系统设置", "运行日志"]
)
st.sidebar.caption("所有 AI 分析均为辅助判断。未独立核实的信息标记为 unverified。")
if not key:
    st.markdown(
        '<div class="aih-login-eyebrow">AI Intelligence Hub</div>',
        unsafe_allow_html=True,
    )
    st.title("让分散的信息，成为可追踪的情报。")
    st.markdown(
        '<p class="aih-login-lede">把多行业实时监控、事件关联和每日简报放在同一条可追踪的流水线里。'
        "进入前只需要本机管理员密钥。</p>",
        unsafe_allow_html=True,
    )
    with st.container(border=True, key=theme.card_key("login")):
        st.markdown("### 如何进入")
        st.markdown(
            "1. 在左侧「管理员密钥」输入本机 `.env` 文件中的 `ADMIN_API_KEY`。\n"
            "2. 按回车后进入左侧已选择的工作空间（默认「概览」）。"
        )
        st.caption("密钥只在当前会话用于本机 API 请求；未输入时不会加载业务数据。窄屏请先展开左侧栏。")
    st.stop()


class ApiError(RuntimeError):
    """User-facing API failure a caller can report without stopping the whole page."""


def api(path: str, method: str = "GET", body=None, params=None, *, soft: bool = False):
    def fail(message: str) -> None:
        if soft:
            raise ApiError(message)
        st.error(message)
        st.stop()

    try:
        response = httpx.request(
            method,
            base + "/api/v1/" + path,
            headers={"X-API-Key": key},
            json=body,
            params=params,
            timeout=90,
            trust_env=False,
        )
    except httpx.HTTPError:
        fail("无法连接 API 服务，请检查服务状态。")
    try:
        data = response.json()
    except ValueError:
        # Gateways/502-503 pages are often HTML; never render or relay them.
        fail(f"API 服务暂不可用（HTTP {response.status_code}），请稍后重试。")
    if response.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else None
        if response.status_code >= 500 or not isinstance(detail, str) or not detail:
            fail(f"请求失败（HTTP {response.status_code}），请稍后重试。")
        else:
            fail(f"请求失败（HTTP {response.status_code}）：{detail[:200]}")
    return data


# Render the page heading before the first API call so a gateway error leaves a
# readable page instead of a blank screen.
show_heading = not (page == "实时新闻" and st.session_state.get("_reader"))
if show_heading:
    st.title(page)

config = api("settings")
tz = ZoneInfo(config["timezone"])


def localtime(value):
    if not value:
        return "—"
    return datetime.fromisoformat(value).astimezone(tz).strftime("%m-%d %H:%M")


# Display-only threshold: the scheduler heartbeats about every 10s and the
# worker about every 30s, but a stalled collection may delay a worker beat.
HEARTBEAT_RECENT = timedelta(minutes=5)


def heartbeat_status(value, current=None):
    """Describe a heartbeat by age only; never infer process health from it."""
    if not value:
        return "暂无记录"
    try:
        seen = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return "时间不可解析"
    if seen.tzinfo is None:
        return "时间缺少时区"
    current = current or datetime.now(tz)
    age = current - seen.astimezone(tz)
    if age.total_seconds() < 0:
        return "记录时间在未来，无法判断"
    if age > HEARTBEAT_RECENT:
        return "未观测到近期心跳"
    return "近期有心跳"


def heartbeat_line(value):
    status = heartbeat_status(value)
    if status in {"暂无记录", "时间不可解析", "时间缺少时区"}:
        return status
    return f"{localtime(value)} · {status}"


def escaped_block(text, class_name: str = "aih-external-text", tag: str = "div") -> None:
    """Render external text as an escaped HTML block, never as Markdown."""
    st.markdown(
        f'<{tag} class="{class_name}">{html.escape(str(text or ""))}</{tag}>',
        unsafe_allow_html=True,
    )


SOURCE_STATUS_LABELS = {
    "healthy": "上次请求成功",
    "backoff": "上次请求失败",
    "error": "上次请求失败",
    "idle": "尚未采集",
    "unverified": "尚未采集",
    "deleted": "已归档",
}
SOURCE_FILTER_VALUES = ["all", "enabled", "disabled", "errors"]
SOURCE_FILTER_LABELS = {
    "all": "全部",
    "enabled": "已启用",
    "disabled": "未启用",
    "errors": "当前有错误记录",
}


def source_status_label(status) -> str:
    raw = str(status or "").strip()
    return SOURCE_STATUS_LABELS.get(raw, raw or "未知")


def source_enabled_label(enabled) -> str:
    return "已启用" if enabled else "未启用"


def source_freshness_label(value) -> str:
    raw = str(value or "").strip()
    return {
        "recent": "7天内",
        "older_than_7_days": "超过7天",
        "unknown": "日期未知",
        "no_articles": "尚无文章",
    }.get(raw, raw or "未知")


def source_success_rate(source: dict):
    checked = source.get("checked_count") or 0
    if not checked:
        return None
    return 100 * (source.get("success_count") or 0) / checked


def source_has_error(source: dict) -> bool:
    return bool(source.get("last_error"))


def source_matches_filter(source: dict, value: str) -> bool:
    if value == "enabled":
        return bool(source.get("enabled"))
    if value == "disabled":
        return not source.get("enabled")
    if value == "errors":
        return source_has_error(source)
    return True


def source_overview_rows(sources: list[dict], localtime) -> list[dict]:
    return [
        {
            "来源": source["name"],
            "启用配置": source_enabled_label(source.get("enabled")),
            "上次采集状态": source_status_label(source.get("status")),
            "最近采集成功": localtime(source.get("last_success_at")),
            "最近内容发布": localtime(source.get("latest_published_at")),
            "内容新鲜度": source_freshness_label(source.get("freshness_status")),
            "入库文章": source.get("article_count", 0),
        }
        for source in sources
    ]


def source_diagnostic_rows(sources: list[dict], industry_names: dict, localtime) -> list[dict]:
    rows = []
    for source in sources:
        industries = " / ".join(
            industry_names.get(identity, identity) for identity in (source.get("industry_ids") or [])
        )
        trust = source.get("trust_level")
        rows.append(
            {
                "来源 ID": source.get("id") or "—",
                "来源": source.get("name") or "—",
                "类型": source.get("source_type") or "—",
                "URL": source.get("url") or "—",
                "关联行业": industries or "—",
                "语言": source.get("language") or "—",
                "国家": source.get("country") or "—",
                "优先度": source.get("priority"),
                "可信度": float(trust) if trust is not None else None,
                "启用配置": source_enabled_label(source.get("enabled")),
                "原始状态": source.get("status") or "—",
                "采集状态": source_status_label(source.get("status")),
                "检查次数": source.get("checked_count", 0),
                "成功次数": source.get("success_count", 0),
                "连续失败次数": source.get("error_count", 0),
                "成功率（%）": source_success_rate(source),
                "更新间隔（分钟）": source.get("poll_interval_seconds", 0) // 60,
                "最近检查": localtime(source.get("last_checked_at")),
                "最近采集成功": localtime(source.get("last_success_at")),
                "下次轮询": localtime(source.get("next_poll_at")),
                "最近内容发布": localtime(source.get("latest_published_at")),
                "内容新鲜度": source_freshness_label(source.get("freshness_status")),
                "新鲜度原始值": source.get("freshness_status") or "—",
                "入库文章": source.get("article_count", 0),
                "内容年龄（天）": source.get("content_age_days"),
                "推定日期": source.get("inferred_date_count", 0),
                "未来日期异常": source.get("future_date_count", 0),
                "最近错误": source.get("last_error") or "—",
                "ETag": source.get("etag") or "—",
                "Last-Modified": source.get("last_modified") or "—",
                "创建时间": localtime(source.get("created_at")),
                "更新时间": localtime(source.get("updated_at")),
            }
        )
    return rows


if show_heading:
    context_col, refresh_col = st.columns([3, 1], vertical_alignment="center")
    context_col.caption(
        f"多行业实时监控 / {datetime.now(tz).strftime('%Y年%m月%d日')} / {config['timezone']}"
    )
    if refresh_col.button("刷新数据", type="secondary", width="stretch"):
        st.rerun()
industries = api("industries")
industry_names = {v["id"]: v["name"] for v in industries}

if page == "概览":

    @st.fragment(run_every="20s")
    def overview_metrics():
        stats = api("stats/overview")
        with st.container(key="aih-overview-metrics"):
            columns = st.columns(4)
            for col, label, field in zip(
                columns,
                ["24h 采集文章", "24h 新事件", "24h 重大事件", "启用数据源"],
                ["articles_24h", "events_24h", "critical_24h", "active_sources"],
                strict=False,
            ):
                col.metric(label, stats[field])
        st.caption(
            "指标统计最近 24 小时；下方列表与心跳是库中最新记录，不代表发生在 24 小时内或当前正在运行。"
        )
        if stats["articles_24h"] == 0:
            st.info(
                "最近 24 小时没有新文章。可前往「实时新闻」查看库中历史文章，"
                "或在下方图表中将范围切换为最近 30 天；图表范围不会自动改变。"
            )

    overview_metrics()
    from visuals import render_visuals

    render_visuals(api)
    st.divider()

    @st.fragment(run_every="20s")
    def recent_news():
        stats = api("stats/overview")
        left, right = st.columns([2, 1])
        with left:
            st.subheader("最新情报")
            st.caption("库中最新文章，不限 24 小时。")
            for item in api("articles", params={"limit": 8})["items"]:
                with st.container(border=True, key=theme.card_key("overview-" + item["id"])):
                    st.caption(
                        f"{localtime(item['published_at'])} · {item['importance_score']} 分 · {', '.join(industry_names.get(i, i) for i in item['industry_ids'])}"
                    )
                    escaped_block(item["title"], "aih-news-title", "h3")
                    st.link_button("阅读原文 ↗", item["original_url"])
        with right:
            st.subheader("运行脉搏")
            rate = stats["collection_success_rate"]
            st.metric("采集成功率", f"{rate:.1%}" if rate is not None else "暂无采集")
            st.metric("待处理任务", stats["backlog"])
            st.write("最近采集：", localtime(stats["last_collection"]))
            st.write("调度心跳：", heartbeat_line(stats["scheduler_last_seen"]))
            st.write("Worker 心跳：", heartbeat_line(stats.get("worker_last_seen")))
            st.write("分析模式：", "AI + 规则" if stats["ai_enabled"] else "规则降级模式")
            st.metric("累计估算 AI 费用", f"¥{stats['ai_estimated_cost_cny']:.4f}")
            st.caption(
                "心跳仅按最近记录时间判断（5 分钟内视为近期），不代表服务健康；"
                "延迟指标需基于实际采集和通知数据评估。"
            )

    recent_news()

elif page == "实时新闻":
    from news import render_news

    render_news(api, config, industry_names, tz, localtime)

elif page == "重大事件":
    st.caption("近似标题报道会保守关联到同一事件；报道数量不代表独立证实。历史事件暂不自动合并。")
    score = st.slider("最低重要性评分", 0, 100, 60)
    for event in api("events", params={"min_score": score})["items"]:
        with st.expander(
            f"{event['importance_score']}分 · {event.get('article_count', 1)}篇报道 · {event['title']}"
        ):
            detail = api("events/" + event["id"])
            escaped_block(event["summary"])
            st.caption(
                f"验证状态：{event['verification_status']} · 首次发现 {localtime(event['first_seen_at'])}"
                f" · 最近更新 {localtime(event['last_updated_at'])}"
            )
            st.write("报道时间线")
            for article in detail["articles"]:
                label = (
                    "近似标题关联 · 未核实"
                    if article.get("relation_type") == "similar_title_unverified"
                    else "初始报道"
                )
                st.caption(
                    f"{localtime(article['published_at'])} · {article.get('source_name', '')} · {label}"
                )
                escaped_block(article["title"])
                if article.get("publication_time_inferred"):
                    st.caption("来源未提供发布时间，以上时间为发现时间。")
                st.link_button("阅读来源原文", article["original_url"], key=article["id"])
            if detail["notifications"]:
                with st.expander("通知记录"):
                    st.dataframe(detail["notifications"], use_container_width=True)

elif page == "行业管理":
    chosen = st.selectbox(
        "编辑行业", ["new"] + list(industry_names), format_func=lambda x: industry_names.get(x, "＋ 新建行业")
    )
    item = next((i for i in industries if i["id"] == chosen), {})
    with st.form("industry"):
        name = st.text_input("行业名称", item.get("name", ""))
        slug = st.text_input("英文标识", item.get("slug", ""))
        description = st.text_area("说明", item.get("description", ""))
        keywords = st.text_area("关键词 / 中英文同义词（逗号分隔）", ",".join(item.get("keywords", [])))
        exclusions = st.text_input("排除词（逗号分隔）", ",".join(item.get("exclusions", [])))
        companies = st.text_input("关注企业 / 人物（逗号分隔）", ",".join(item.get("companies", [])))
        threshold = st.number_input("重大告警阈值", 0, 100, item.get("alert_threshold", 85))
        enabled = st.checkbox("启用", item.get("enabled", True))
        channels = st.multiselect(
            "通知渠道", ["telegram", "feishu"], item.get("channels", ["telegram", "feishu"])
        )
        if st.form_submit_button("保存行业", type="primary"):

            def split(v):
                return [s.strip() for s in v.replace("，", ",").split(",") if s.strip()]

            body = dict(
                name=name,
                slug=slug,
                description=description,
                keywords=split(keywords),
                exclusions=split(exclusions),
                companies=split(companies),
                alert_threshold=threshold,
                enabled=enabled,
                channels=channels,
            )
            api("industries" + ("/" + chosen if item else ""), "PATCH" if item else "POST", body)
            st.rerun()
    if item and st.button("删除此行业"):
        api("industries/" + chosen, "DELETE")
        st.rerun()

elif page == "数据源管理":
    sources = api("sources")
    st.caption(
        "抓取成功只代表最近一次请求成功，不代表当前在线；内容新鲜度只反映库中发布时间，不代表来源故障。"
    )
    if sources:
        total = len(sources)
        enabled_count = sum(bool(source.get("enabled")) for source in sources)
        error_count = sum(source_has_error(source) for source in sources)
        a, b, c = st.columns(3)
        a.metric("来源总数", total)
        b.metric("已启用配置", enabled_count)
        c.metric("当前有错误记录", error_count)
        st.caption(
            "统计基于全部来源；“当前有错误记录”指最近保留的错误信息，不代表连续失败次数；"
            "列表筛选只影响下方表格，不改变编辑下拉、导入导出或来源配置。"
        )
        filter_value = st.radio(
            "列表筛选",
            SOURCE_FILTER_VALUES,
            format_func=SOURCE_FILTER_LABELS.get,
            horizontal=True,
            key="source_list_filter",
        )
        filtered = [source for source in sources if source_matches_filter(source, filter_value)]
        if filtered:
            st.dataframe(
                source_overview_rows(filtered, localtime),
                use_container_width=True,
                hide_index=True,
            )
            with st.expander("展开完整诊断表（成功率、间隔、错误、日期异常等）", expanded=False):
                st.caption(
                    "横向滚动查看全部字段；原始状态保留内部值，采集状态为最近一次请求结果的中文历史含义。"
                )
                st.dataframe(
                    source_diagnostic_rows(filtered, industry_names, localtime),
                    use_container_width=True,
                    hide_index=True,
                )
        else:
            st.info("当前筛选没有来源。可切换筛选，或在下方新建来源。")
    else:
        st.info("尚无来源。可在下方新建来源，或导入 JSON。")
    export_fields = [
        "name",
        "source_type",
        "url",
        "industry_ids",
        "language",
        "country",
        "priority",
        "trust_level",
        "poll_interval_seconds",
        "enabled",
    ]
    st.download_button(
        "导出来源清单",
        json.dumps([{k: s[k] for k in export_fields} for s in sources], ensure_ascii=False, indent=2),
        "sources.json",
    )
    uploaded = st.file_uploader("导入来源 JSON（按名称更新）", type="json")
    if uploaded and st.button("执行导入"):
        try:
            records = json.loads(uploaded.getvalue())
            if not isinstance(records, list) or len(records) > 100:
                raise ValueError()
        except ValueError:
            st.error("需要最多 100 条来源的 JSON 数组")
            st.stop()
        for record in records:
            old = next((s for s in sources if s["name"] == record.get("name")), None)
            api("sources" + ("/" + old["id"] if old else ""), "PATCH" if old else "POST", record)
        st.rerun()
    names = {s["id"]: s["name"] for s in sources}
    chosen = st.selectbox(
        "编辑来源", ["new"] + list(names), format_func=lambda x: names.get(x, "＋ 新建来源")
    )
    item = next((s for s in sources if s["id"] == chosen), {})
    with st.form("source"):
        name = st.text_input("名称", item.get("name", ""))
        url = st.text_input("公开 RSS / API 地址", item.get("url", ""))
        kind = st.selectbox("格式", ["rss", "gdelt"], index=1 if item.get("source_type") == "gdelt" else 0)
        ids = st.multiselect(
            "关联行业",
            list(industry_names),
            [i for i in item.get("industry_ids", []) if i in industry_names],
            format_func=industry_names.get,
        )
        interval = st.number_input("轮询间隔（秒）", 120, 86400, item.get("poll_interval_seconds", 900))
        trust = st.slider("来源可信度（不等于事实已证实）", 0.0, 1.0, float(item.get("trust_level", 0.7)))
        enabled = st.checkbox("启用此来源（请先确认使用条款）", item.get("enabled", False))
        if st.form_submit_button("保存来源", type="primary"):
            body = dict(
                name=name,
                url=url,
                source_type=kind,
                industry_ids=ids,
                poll_interval_seconds=interval,
                trust_level=trust,
                enabled=enabled,
            )
            api("sources" + ("/" + chosen if item else ""), "PATCH" if item else "POST", body)
            st.rerun()
    if item:
        a, b, c = st.columns(3)
        if a.button("检测可访问性"):
            st.json(api("sources/" + chosen + "/test", "POST"))
        if b.button("立即采集"):
            st.json(api("sources/" + chosen + "/run", "POST"))
        if c.button("删除 / 归档来源"):
            api("sources/" + chosen, "DELETE")
            st.rerun()

elif page == "简报":
    from briefs import render_briefs

    render_briefs(api, tz)

elif page == "系统设置":
    a, b = st.columns(2)
    a.metric("每日 AI 调用上限", config["ai_daily_request_limit"])
    b.metric("月估算预算", f"¥{config['monthly_ai_budget_cny']:.0f}")
    st.write("自动备份：", "已启用" if config.get("backup_enabled") else "未启用")
    st.write("每日报告：", f"每天 {config.get('daily_digest_hour', 8):02d}:00 归档前一天的情报")
    with st.expander("查看详细配置"):
        st.json(config)
    st.info("敏感配置保存在服务器 .env；修改后需重新创建容器。此页面仅展示安全参数，不显示密钥。")
    st.code(
        "AI_ENABLED=true\nAI_API_BASE_URL=https://provider.example/v1\nAI_MODEL=your-model\nAI_API_KEY=在服务器配置"
    )
    for channel in ["telegram", "feishu"]:
        enabled = next(
            (value["enabled"] for value in config["channels"] if value["channel"] == channel), False
        )
        if not enabled:
            continue
        if st.button(f"发送 {channel} 测试通知"):
            st.json(api("notifications/test", "POST", params={"channel": channel}))

elif page == "运行日志":
    job_status_labels = {
        "all": "全部",
        "failed": "失败",
        "running": "运行中",
        "pending": "等待中",
        "retry_wait": "等待重试",
        "completed": "已完成",
    }
    job_type_labels = {
        "collect": "采集",
        "analyze": "分析",
        "daily_digest": "日报归档",
        "backup": "备份",
        "notify": "通知",
        "notify_batch": "通知汇总",
    }
    retryable_types = {"collect", "analyze", "daily_digest", "backup"}

    st.subheader("持久化任务状态")
    status = st.selectbox("状态筛选", list(job_status_labels), format_func=job_status_labels.get)
    jobs = api("jobs", params={"limit": 100, "status": status})
    st.dataframe(
        [
            {
                "任务类别": job_type_labels.get(job["job_type"], job["job_type"]),
                "状态": job_status_labels.get(job["status"], job["status"]),
                "尝试次数": job["attempt_count"],
                "计划时间": localtime(job["scheduled_at"]),
                "最近错误": job["last_error"] or "—",
                "任务 ID": job["id"],
            }
            for job in jobs
        ],
        use_container_width=True,
    )
    with st.expander("原始任务详情"):
        st.dataframe(jobs, use_container_width=True)

    retryable = [job for job in jobs if job["status"] == "failed" and job["job_type"] in retryable_types]
    if retryable:
        # Options are the real job IDs (unique); the label only formats them for
        # people. Truncated IDs are never used as dict keys, which would drop
        # jobs that share an 8-character prefix.
        labels = {
            job["id"]: (
                f"{job_type_labels.get(job['job_type'], job['job_type'])}"
                f" · {job['id']} · {job['last_error'] or '失败'}"
            )
            for job in retryable
        }
        chosen = st.multiselect("选择要重试的失败任务", list(labels), format_func=labels.get)
        if st.button("重试所选失败任务"):
            if not chosen:
                st.warning("请先选择失败任务。")
            else:
                queued = deduplicated = 0
                failures = []
                for job_id in chosen:
                    # soft=True keeps one rejected task from stopping the loop and
                    # hiding results already queued earlier in this batch.
                    try:
                        result = api("jobs/" + job_id + "/retry", "POST", soft=True)
                    except ApiError as exc:
                        failures.append(f"{job_id}：{exc}")
                        continue
                    if result.get("queued"):
                        queued += 1
                    else:
                        deduplicated += 1
                if queued:
                    st.success(f"已入队 {queued} 个重试任务。这里只创建任务，需 Worker 运行后才会执行。")
                if deduplicated:
                    st.info(f"{deduplicated} 个任务已在队列或已有同类活动任务，未重复入队。")
                if failures:
                    st.error("以下任务未能重试：" + "；".join(failures))
    else:
        st.caption("当前筛选下没有可手动重试的失败任务。通知类任务不支持手动重试。")

    st.subheader("通知投递")
    st.dataframe(api("notifications"), use_container_width=True)
    st.subheader("运行指标")
    st.dataframe(api("metrics"), use_container_width=True)
