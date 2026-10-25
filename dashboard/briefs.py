"""Page through report entries while keeping full Markdown available for download."""

import hashlib
import re
from datetime import datetime, timedelta

import streamlit as st

REPORT_TIME_FORMAT = "%Y-%m-%d %H:%M"


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


def safe_markdown(text: str) -> str:
    """Neutralize Markdown image markers; normal links and raw download stay untouched."""
    return str(text or "").replace("![", "!\\[")


def report_time(value, tz):
    """Parse an API timestamp only when it carries an explicit timezone."""
    if not value or tz is None:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(tz)


def report_header(header: str, report: dict | None, tz, entry_count: int) -> None:
    report = report or {}
    title = str(report.get("title") or "").strip()
    if not title:
        title = next((line[2:].strip() for line in header.splitlines() if line.startswith("# ")), "简报")
    st.subheader(safe_markdown(title))
    tz_name = getattr(tz, "key", str(tz)) if tz else ""
    suffix = f"（{tz_name}）" if tz_name else ""
    period_start = report_time(report.get("period_start"), tz)
    period_end = report_time(report.get("period_end"), tz)
    generated_at = report_time(report.get("generated_at"), tz)
    if period_start and period_end:
        st.caption(
            f"统计区间{suffix}：{period_start.strftime(REPORT_TIME_FORMAT)}"
            f" 至 {period_end.strftime(REPORT_TIME_FORMAT)}（结束时刻不含）"
        )
    if generated_at:
        st.caption(f"生成时间{suffix}：{generated_at.strftime(REPORT_TIME_FORMAT)}")
    # Digest.article_count is the article total for the period, not the selected entries.
    total_articles = report.get("article_count")
    if (
        isinstance(total_articles, int)
        and not isinstance(total_articles, bool)
        and total_articles >= 0
        and period_start
        and period_end
    ):
        st.caption(f"统计范围内文章总量：{total_articles} 篇 · 入选情报：{entry_count} 条")
    else:
        st.caption(f"入选情报：{entry_count} 条")
    if header.strip():
        with st.expander("查看原始报告时间范围与说明", expanded=False):
            st.markdown(safe_markdown(re.sub(r"(?m)^# ", "### ", header)), unsafe_allow_html=False)


def is_brief_meta(line: str) -> bool:
    return "未独立核验" in line or re.match(r"^\d+\s*分(?:\s|·|$)", line) is not None


def entry_preview(entry: str) -> tuple[str | None, str | None]:
    """Extract title and one-line preview without inventing fields for old formats."""
    lines = [line.strip() for line in entry.splitlines() if line.strip()]
    if not lines:
        return None, None
    first = lines[0]
    if first.startswith("## "):
        title = first[3:].strip() or None
        summary = None
        if len(lines) >= 3 and is_brief_meta(lines[1]):
            candidate = lines[2]
            if not candidate.startswith(("来源：", "AI 摘要", "来源摘要", "来源未提供")):
                summary = candidate
        return title, summary
    custom = re.match(r"^- \[(.*)\]\((https?://[^)\s]+)\)(.*)$", first)
    if custom:
        title = custom.group(1).strip() or None
        summary = None
        if len(lines) >= 2:
            candidate = lines[1]
            if not candidate.startswith(("来源：", "AI 摘要", "来源摘要", "来源未提供")):
                summary = candidate
        return title, summary
    return None, None


def preview_text(text: str, length: int = 180) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= length else text[:length].rstrip() + "…"


def pager(
    namespace: str,
    location: str,
    page_key: str,
    pages: int,
    page: int,
    size: int,
    total: int,
    *,
    include_page_input: bool,
) -> None:
    suffix = "" if location == "top" else "_bottom"
    a, b, c = st.columns([1, 2, 1], vertical_alignment="bottom")
    a.button(
        "← 上一页",
        key=f"{namespace}{suffix}_previous",
        disabled=page <= 1,
        on_click=move_page,
        args=(page_key, -1, pages),
        width="stretch",
    )
    if include_page_input:
        b.number_input("简报页码", min_value=1, max_value=pages, step=1, key=page_key)
    else:
        b.caption(f"第 {page} / {pages} 页")
    c.button(
        "下一页 →",
        key=f"{namespace}{suffix}_next",
        disabled=page >= pages,
        on_click=move_page,
        args=(page_key, 1, pages),
        width="stretch",
    )
    start = (page - 1) * size
    st.caption(
        f"第 {page} / {pages} 页 · 本页第 {start + 1}–{min(start + size, total)} 条 · 共 {total} 条入选情报"
    )


def paged_report(markdown: str, identity: str, namespace: str, report: dict | None = None, tz=None) -> None:
    header, entries = split_report(markdown)
    report_header(header, report, tz, len(entries))
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
    page = st.session_state[page_key]
    pager(
        namespace,
        "top",
        page_key,
        pages,
        page,
        size,
        len(entries),
        include_page_input=True,
    )
    st.caption("条目预览未经独立核验，完整来源与评分见展开内容；AI 摘要不代表事实确认。")
    start = (page - 1) * size
    for offset, entry in enumerate(entries[start : start + size]):
        title, summary = entry_preview(entry)
        with st.container(border=True):
            if title:
                st.markdown(f"#### {safe_markdown(title)}", unsafe_allow_html=False)
            else:
                st.caption("展开查看完整条目与来源。")
            if summary:
                st.markdown(safe_markdown(preview_text(summary)), unsafe_allow_html=False)
            elif title:
                st.caption("展开查看完整条目与来源。")
            with st.expander("展开完整条目", key=f"{namespace}-entry-{start + offset}", expanded=False):
                st.markdown(safe_markdown(re.sub(r"(?m)^## ", "#### ", entry)), unsafe_allow_html=False)
    pager(
        namespace,
        "bottom",
        page_key,
        pages,
        page,
        size,
        len(entries),
        include_page_input=False,
    )
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
        paged_report(report["markdown"], chosen, "daily_brief", report=report, tz=tz)
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
            paged_report(custom["markdown"], identity, "custom_brief", report=custom, tz=tz)
