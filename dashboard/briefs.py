"""Page through report entries while keeping full Markdown available for download."""

import hashlib
import re
from datetime import datetime, timedelta

import streamlit as st


def split_report(markdown: str) -> tuple[str, list[str]]:
    boundaries = list(re.finditer(r"(?m)^(?:## |\- \[)", markdown))
    if not boundaries:
        return markdown, []
    return markdown[: boundaries[0].start()].strip(), [
        markdown[
            match.start() : boundaries[index + 1].start() if index + 1 < len(boundaries) else len(markdown)
        ].strip()
        for index, match in enumerate(boundaries)
    ]


def move_page(key: str, delta: int, pages: int) -> None:
    st.session_state[key] = min(pages, max(1, st.session_state[key] + delta))


def paged_report(markdown: str, identity: str, namespace: str) -> None:
    header, entries = split_report(markdown)
    st.markdown(re.sub(r"(?m)^# ", "### ", header), unsafe_allow_html=False)
    if not entries:
        return
    size = st.selectbox("每页简报条目", [5, 10, 20], key=namespace + "_size")
    pages = max(1, (len(entries) + size - 1) // size)
    page_key, signature_key = namespace + "_page", namespace + "_signature"
    signature = identity + ":" + str(size)
    if st.session_state.get(signature_key) != signature:
        st.session_state[signature_key] = signature
        st.session_state[page_key] = 1
    st.session_state[page_key] = min(pages, max(1, st.session_state.get(page_key, 1)))
    a, b, c = st.columns([1, 2, 1], vertical_alignment="bottom")
    a.button(
        "← 上一页",
        key=namespace + "_previous",
        disabled=st.session_state[page_key] <= 1,
        on_click=move_page,
        args=(page_key, -1, pages),
        width="stretch",
    )
    b.number_input("简报页码", min_value=1, max_value=pages, step=1, key=page_key)
    c.button(
        "下一页 →",
        key=namespace + "_next",
        disabled=st.session_state[page_key] >= pages,
        on_click=move_page,
        args=(page_key, 1, pages),
        width="stretch",
    )
    page = st.session_state[page_key]
    start = (page - 1) * size
    st.caption(
        f"第 {page} / {pages} 页 · 本页第 {start + 1}–{min(start + size, len(entries))} 条 · 共 {len(entries)} 条入选情报"
    )
    for entry in entries[start : start + size]:
        with st.container(border=True):
            st.markdown(re.sub(r"^## ", "#### ", entry), unsafe_allow_html=False)
    st.caption("完整内容可从上方下载；切换每页条数或简报后，从第一页开始。")


def render_briefs(api, tz) -> None:
    st.caption("每日简报自动归档，分页阅读；复用已有分析，不额外调用模型或发送消息。")
    if st.button("生成昨日日报"):
        result = api("reports/daily", "POST")
        st.success("报告已归档，请在下方查看。" if result.get("report_id") else "已加入队列，稍后刷新查看。")
    archives = api("reports")
    if archives:
        titles = {item["id"]: item["title"] for item in archives}
        chosen = st.selectbox("已归档简报", list(titles), format_func=titles.get)
        report = api("reports/" + chosen)
        st.download_button("下载完整每日简报", report["markdown"], "aih-daily.md", mime="text/markdown")
        paged_report(report["markdown"], chosen, "daily_brief")
    else:
        st.info("尚无归档简报。每天按设定时间生成，也可手动生成昨日报告。")
    with st.expander("自定义时间范围", expanded=bool(st.session_state.get("_custom_brief"))):
        start = st.date_input("起始日期", datetime.now(tz).date() - timedelta(days=1))
        end = st.date_input("结束日期（包含当天）", datetime.now(tz).date())
        if st.button("生成 Markdown 简报"):
            if end < start:
                st.error("结束日期不能早于起始日期。")
            else:
                st.session_state["_custom_brief"] = api(
                    "reports",
                    "POST",
                    {
                        "start": datetime.combine(start, datetime.min.time(), tzinfo=tz).isoformat(),
                        "end": datetime.combine(
                            end + timedelta(days=1), datetime.min.time(), tzinfo=tz
                        ).isoformat(),
                    },
                )
        custom = st.session_state.get("_custom_brief")
        if custom:
            st.download_button(
                "下载完整自定义简报", custom["markdown"], "aih-report.md", mime="text/markdown"
            )
            identity = hashlib.sha256(custom["markdown"].encode()).hexdigest()
            paged_report(custom["markdown"], identity, "custom_brief")
