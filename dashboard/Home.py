import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import streamlit as st

st.set_page_config(page_title="AI Intelligence Hub", page_icon="◈", layout="wide")
st.markdown(
    """<style>
.stApp {background:#0c111b;color:#e7edf6} [data-testid=stSidebar]{background:#121b2a}
h1,h2,h3{letter-spacing:-.025em} [data-testid=stMetric]{background:#152033;padding:18px;border:1px solid #28374d;border-radius:12px}
.block-container{padding-top:2rem} a{color:#67dac3!important}
</style>""",
    unsafe_allow_html=True,
)

st.sidebar.title("◈ AIH 情报中心")
st.sidebar.caption("AI INTELLIGENCE HUB · V0.1")
key = st.sidebar.text_input("管理员密钥", type="password", value="")
base = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
# No key auto-filled or sent to browser. Enter explicitly per session.
page = st.sidebar.radio(
    "工作空间", ["概览", "实时新闻", "重大事件", "行业管理", "数据源管理", "简报", "系统设置", "运行日志"]
)
st.sidebar.caption("所有 AI 分析均为辅助判断。未独立核实的信息标记为 unverified。")
if not key:
    st.title("让分散的信息，成为可追踪的情报。")
    st.info("在左侧输入 .env 中的 ADMIN_API_KEY，进入个人情报工作空间。")
    st.stop()


def api(path: str, method: str = "GET", body=None, params=None):
    try:
        response = httpx.request(
            method, base + "/api/v1/" + path, headers={"X-API-Key": key}, json=body, params=params, timeout=90
        )
        if response.status_code >= 400:
            st.error(f"请求失败 ({response.status_code})：{response.json().get('detail', '')}")
            st.stop()
        return response.json()
    except httpx.HTTPError:
        st.error("无法连接 API 服务，请检查服务状态。")
        st.stop()


config = api("settings")
tz = ZoneInfo(config["timezone"])


def localtime(value):
    if not value:
        return "—"
    return datetime.fromisoformat(value).astimezone(tz).strftime("%m-%d %H:%M")


st.title(page)
st.caption(f"多行业实时监控 / {datetime.now(tz).strftime('%Y年%m月%d日')} / {config['timezone']}")
if st.button("刷新数据", type="secondary"):
    st.rerun()
industries = api("industries")
industry_names = {v["id"]: v["name"] for v in industries}

if page == "概览":

    @st.fragment(run_every="20s")
    def overview():
        stats = api("stats/overview")
        columns = st.columns(4)
        for col, label, field in zip(
            columns,
            ["24h 采集文章", "24h 新事件", "24h 重大事件", "启用数据源"],
            ["articles_24h", "events_24h", "critical_24h", "active_sources"],
            strict=False,
        ):
            col.metric(label, stats[field])
        left, right = st.columns([2, 1])
        with left:
            st.subheader("最新情报")
            for item in api("articles", params={"limit": 8})["items"]:
                with st.container(border=True):
                    st.caption(
                        f"{localtime(item['published_at'])} · {item['importance_score']} 分 · {', '.join(industry_names.get(i, i) for i in item['industry_ids'])}"
                    )
                    st.write(item["title"])
                    st.link_button("阅读原文 ↗", item["original_url"])
        with right:
            st.subheader("运行脉搏")
            rate = stats["collection_success_rate"]
            st.metric("采集成功率", f"{rate:.1%}" if rate is not None else "暂无采集")
            st.metric("待处理任务", stats["backlog"])
            st.write("最近采集：", localtime(stats["last_collection"]))
            st.write("调度心跳：", localtime(stats["scheduler_last_seen"]))
            st.write("Worker 心跳：", localtime(stats.get("worker_last_seen")))
            st.write("分析模式：", "AI + 规则" if stats["ai_enabled"] else "规则降级模式")
            st.metric("累计估算 AI 费用", f"¥{stats['ai_estimated_cost_cny']:.4f}")
            st.caption("延迟指标需基于实际采集和通知数据评估。")

    overview()

elif page == "实时新闻":
    a, b, c, d = st.columns(4)
    q = a.text_input("全文搜索")
    industry_id = b.selectbox(
        "行业", [""] + list(industry_names), format_func=lambda x: industry_names.get(x, "全部行业")
    )
    sort = c.selectbox(
        "排序",
        ["published_at", "collected_at", "importance_score"],
        format_func=lambda x: {
            "published_at": "发布时间",
            "collected_at": "发现时间",
            "importance_score": "重要性",
        }[x],
    )
    lang = d.selectbox("语言", ["", "zh", "en"])
    source_list = api("sources")
    source_names = {s["id"]: s["name"] for s in source_list}
    a, b, c = st.columns(3)
    source_id = a.selectbox(
        "来源", [""] + list(source_names), format_func=lambda x: source_names.get(x, "全部来源")
    )
    days = b.selectbox(
        "发布时间范围", [0, 1, 7, 30], format_func=lambda x: "全部时间" if not x else f"最近 {x} 天"
    )
    number = c.number_input("页码", min_value=1, value=1)
    params = {"q": q, "sort": sort, "limit": 20, "offset": (number - 1) * 20}
    if industry_id:
        params["industry_id"] = industry_id
    if lang:
        params["language"] = lang
    if source_id:
        params["source_id"] = source_id
    if days:
        params["start"] = (datetime.now(tz) - timedelta(days=days)).isoformat()
    data = api("articles", params=params)
    st.caption(f"找到 {data['total']} 条文章")
    for item in data["items"]:
        with st.expander(f"{item['importance_score']}分 · {item['title']}"):
            detail = api("articles/" + item["id"])
            st.caption(
                f"发布 {localtime(item['published_at'])} · 发现 {localtime(item['collected_at'])} · {source_names.get(item['source_id'], '')}"
            )
            if item["publication_time_inferred"]:
                st.warning("原文未提供可靠发布时间，当前时间为发现时间。")
            st.write(item["summary"])
            st.link_button("阅读原文 ↗", item["original_url"])
            for analysis in detail["analyses"]:
                if not analysis["analysis_json"]:
                    continue
                result = analysis["analysis_json"]
                st.divider()
                st.caption(f"{analysis['provider']} / {analysis['model']} / {analysis['status']}")
                st.write("摘要：", result.get("summary_zh"))
                st.write("事实提取（尚未独立验证）：", result.get("key_facts"))
                st.write("AI 观点 / 潜在影响：", result.get("impact_analysis"))
                st.write("评分依据：", result.get("reasoning_summary"))
                st.json(result, expanded=False)

elif page == "重大事件":
    score = st.slider("最低重要性评分", 0, 100, 60)
    for event in api("events", params={"min_score": score})["items"]:
        with st.expander(f"{event['importance_score']}分 · {event['title']}"):
            detail = api("events/" + event["id"])
            st.write(event["summary"])
            st.caption(
                f"验证状态：{event['verification_status']} · 首次发现 {localtime(event['first_seen_at'])}"
            )
            for article in detail["articles"]:
                st.write(article["title"])
                st.link_button("来源原文", article["original_url"], key=article["id"])
            st.write("通知记录")
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
    st.dataframe(
        [
            {
                k: s[k]
                for k in [
                    "name",
                    "source_type",
                    "enabled",
                    "status",
                    "poll_interval_seconds",
                    "last_success_at",
                    "last_error",
                    "success_count",
                    "checked_count",
                ]
            }
            for s in sources
        ],
        use_container_width=True,
    )
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
    start = st.date_input("起始日期", datetime.now(tz).date() - timedelta(days=1))
    end = st.date_input("结束日期（包含当天）", datetime.now(tz).date())
    if st.button("生成 Markdown 简报"):
        result = api(
            "reports",
            "POST",
            {
                "start": datetime.combine(start, datetime.min.time(), tzinfo=tz).isoformat(),
                "end": datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=tz).isoformat(),
            },
        )
        st.text(result["markdown"])
        st.download_button("下载简报", result["markdown"], "aih-report.md")

elif page == "系统设置":
    st.json(config)
    st.info("敏感配置保存在服务器 .env；修改后重启服务。此页面仅展示安全参数，不显示密钥。")
    st.code(
        "AI_ENABLED=true\nAI_API_BASE_URL=https://provider.example/v1\nAI_MODEL=your-model\nAI_API_KEY=在服务器配置"
    )
    for channel in ["telegram", "feishu"]:
        if st.button(f"发送 {channel} 测试通知"):
            st.json(api("notifications/test", "POST", params={"channel": channel}))

elif page == "运行日志":
    st.subheader("持久化任务状态")
    st.dataframe(api("jobs", params={"limit": 100}), use_container_width=True)
    st.subheader("通知投递")
    st.dataframe(api("notifications"), use_container_width=True)
    st.subheader("运行指标")
    st.dataframe(api("metrics"), use_container_width=True)
