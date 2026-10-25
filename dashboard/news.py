"""News browsing and a persistent, full-page reading workspace."""

import hashlib
import html
import json
import math
import re
from datetime import datetime, timedelta

import streamlit as st

STYLES = """<style>
.news-top {display:flex;flex-wrap:wrap;align-items:center;gap:.35rem .55rem;margin:0 0 .55rem;font-size:.78rem;line-height:1.6;color:var(--aih-muted)}
.news-source {color:var(--aih-text);font-weight:600;overflow-wrap:anywhere;max-width:100%}
.news-time {white-space:nowrap;color:var(--aih-muted)}
.news-badge {display:inline-flex;align-items:center;max-width:100%;padding:.06rem .48rem;border:1px solid var(--aih-border);border-radius:999px;background:var(--aih-surface);color:var(--aih-muted);font-size:.72rem;line-height:1.5;overflow-wrap:anywhere}
.news-badge-state {color:var(--aih-accent);border-color:rgba(98,223,199,.35);background:rgba(98,223,199,.10)}
.news-badge-score {color:var(--aih-text);background:var(--aih-panel)}
.news-badge-unverified {color:#ffc575;border-color:rgba(255,197,117,.35);background:rgba(255,197,117,.08)}
h3.news-title {font-size:1.23rem;font-weight:600;line-height:1.5;margin:0 0 .25rem;overflow-wrap:anywhere;word-break:break-word;color:var(--aih-text-strong)}
.news-industries {display:flex;flex-wrap:wrap;gap:.35rem;margin:.1rem 0 .2rem}
.news-industry {max-width:100%;padding:.08rem .48rem;border:1px solid var(--aih-border);border-radius:999px;background:var(--aih-surface);color:var(--aih-muted);font-size:.74rem;line-height:1.55;overflow-wrap:anywhere}
.news-summary-label {margin:.75rem 0 .15rem;font-size:.74rem;font-weight:600;letter-spacing:.02em;color:var(--aih-accent)}
.news-summary-label.source {color:var(--aih-muted)}
.news-excerpt {font-size:.96rem;line-height:1.8;color:var(--aih-text);overflow-wrap:anywhere;white-space:pre-wrap}
.reader-kicker {margin:0 0 .35rem;color:var(--aih-accent);font-size:.76rem;font-weight:600;letter-spacing:.05em;overflow-wrap:anywhere}
h1.reader-title {font-size:clamp(1.6rem,3vw,2.3rem);font-weight:650;line-height:1.35;margin:0 0 .45rem;color:var(--aih-text-strong);overflow-wrap:anywhere;word-break:break-word}
.reader-meta {display:flex;flex-wrap:wrap;gap:.2rem .6rem;margin:.2rem 0 .45rem;color:var(--aih-muted);font-size:.82rem;line-height:1.6}
.reader-meta .reader-mode {color:var(--aih-text);font-weight:600}
.reading-prose {line-height:1.9;color:var(--aih-text);white-space:pre-wrap;overflow-wrap:anywhere;margin:0 0 1.05rem}
.reader-section {font-size:1.06rem;font-weight:650;letter-spacing:.02em;margin:1.9rem 0 .55rem;color:var(--aih-accent)}
.reader-fact {padding:12px 18px;margin:0 0 1rem;border-left:3px solid var(--aih-accent-strong);background:var(--aih-panel);border-radius:0 8px 8px 0}
[class*="st-key-aih-card-news-"] [data-testid="stButton"] button {background:var(--aih-accent);border-color:var(--aih-accent);color:#07231d;font-weight:700}
[class*="st-key-aih-card-news-"] [data-testid="stButton"] button:hover {background:var(--aih-accent-strong);border-color:var(--aih-accent-strong);color:#041713}
[data-testid=stMainBlockContainer]{max-width:1240px;padding-bottom:4rem}
</style>"""

CARD_KEY_PREFIX = "aih-card-news-"


READING_STATES = ["all", "unread", "saved", "read"]
READING_LABELS = {"all": "全部", "unread": "未读", "saved": "我的收藏", "read": "已读"}
SORT_OPTIONS = ["collected_at", "published_at", "importance_score"]
SORT_LABELS = {
    "collected_at": "最新发现",
    "published_at": "最新发布",
    "importance_score": "重要优先",
}
LANGUAGE_VALUES = ["", "zh", "en"]
LANGUAGE_LABELS = {"": "全部语言", "zh": "中文", "en": "英文"}
TIME_VALUES = [0, 1, 7, 30]
TIME_LABELS = {0: "全部时间", 1: "最近 1 天", 7: "最近 7 天", 30: "最近 30 天"}
ANALYSIS_STATUSES = ["all", "ai", "rules", "failed"]
ANALYSIS_LABELS = {
    "all": "全部",
    "ai": "已有 AI 分析",
    "rules": "暂无 AI 分析",
    "failed": "AI 等待重试",
}
NEWS_CLEAR_DEFAULTS = {
    "news_q": "",
    "news_reading": "all",
    "news_industry": "",
    "news_source": "",
    "news_language": "",
    "news_days": 0,
    "news_analysis": "all",
}
CLEAR_HELP = "清空搜索、行业、来源、语言、时间与分析状态并回到第 1 页；保留排序、紧凑列表、阅读设置和收藏。"


def request_clear_filters() -> None:
    """Defer the reset to the next run so no instantiated widget state is changed."""
    st.session_state["_news_clear_filters"] = True


def apply_pending_clear() -> None:
    """Apply a requested clear before any filter widget is created."""
    if not st.session_state.pop("_news_clear_filters", False):
        return
    for key, value in NEWS_CLEAR_DEFAULTS.items():
        st.session_state[key] = value
    st.session_state["_news_filters"] = {}
    st.session_state.pop("_news_target_page", None)
    st.session_state["_news_reset_page"] = True


def filter_summary(
    query: str,
    reading: str,
    industry: str,
    source: str,
    language: str,
    days: int,
    analysis: str,
    industries: dict,
    names: dict,
) -> str:
    keyword = f"「{snippet(query, 60)}」" if (query or "").strip() else "无"
    return " · ".join(
        [
            f"关键词：{keyword}",
            f"清单：{READING_LABELS.get(reading, '全部')}",
            f"行业：{industries.get(industry, '未知行业') if industry else '全部行业'}",
            f"来源：{names.get(source, '未知来源') if source else '全部来源'}",
            f"语言：{LANGUAGE_LABELS.get(language, '全部语言')}",
            f"时间：{TIME_LABELS.get(days, '全部时间')}",
            f"分析：{ANALYSIS_LABELS.get(analysis, '全部')}",
        ]
    )


def snippet(text: str, length: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= length:
        return text
    return text[:length].rstrip() + "…"


def news_card_html(item: dict, names: dict, industries: dict, localtime, *, compact: bool = False) -> str:
    """Static card content; every external text value is escaped before HTML."""
    source = str(names.get(item["source_id"], "来源"))
    published = str(localtime(item["published_at"]))
    industry_names = [str(industries.get(value, "未知行业")) for value in (item.get("industry_ids") or [])]
    state = []
    if item.get("bookmarked"):
        state.append('<span class="news-badge news-badge-state">★ 收藏</span>')
    state.append(f'<span class="news-badge">{"已读" if item.get("read_at") else "未读"}</span>')
    industry_html = "".join(
        f'<span class="news-industry">{html.escape(name)}</span>' for name in industry_names
    )
    score = html.escape(str(item.get("importance_score", 0)))
    parts = [
        '<div class="news-top">',
        f'<span class="news-source">{html.escape(source)}</span>',
        f'<span class="news-time">{html.escape(published)}</span>',
        *state,
        f'<span class="news-badge news-badge-score" title="仅表示重要程度，不代表可信度">重要性 {score}</span>',
        '<span class="news-badge news-badge-unverified">未核验</span>',
        "</div>",
        f'<h3 class="news-title">{html.escape(str(item.get("title") or ""))}</h3>',
    ]
    if industry_html:
        parts.append(f'<div class="news-industries">{industry_html}</div>')
    if not compact:
        ai_summary = str(item.get("ai_summary_zh") or "").strip()
        source_summary = str(item.get("summary") or "").strip()
        has_ai = bool(ai_summary)
        summary = ai_summary if has_ai else (source_summary or "来源仅提供标题，阅读原文了解详情。")
        label = "AI 中文摘要" if has_ai else "来源摘要"
        label_class = "" if has_ai else " source"
        parts.append(f'<div class="news-summary-label{label_class}">{label}</div>')
        parts.append(f'<div class="news-excerpt">{html.escape(snippet(summary, 220))}</div>')
    return "".join(parts)


def reading_minutes(text: str) -> int:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    words = len(re.findall(r"[A-Za-z0-9]+", text))
    return max(1, math.ceil(chinese / 450 + words / 200))


def prose(text: str, size: int, css: str = "") -> None:
    # Only escaped text enters HTML; source markup cannot execute or load images.
    st.markdown(
        f'<div class="reading-prose {css}" style="font-size:{size}px">{html.escape(text or "")}</div>',
        unsafe_allow_html=True,
    )


def open_article(identity: str, ids: list[str]) -> None:
    st.session_state["_reader"] = {"id": identity, "ids": ids, "marked": False}


def source_sections(summary: str, content: str | None) -> list[tuple[str, str]]:
    """Return non-duplicate source sections; content is never dropped."""
    summary_text = str(summary or "").strip()
    content_text = str(content or "").strip()
    if content_text == summary_text:
        content_text = ""
    sections = []
    if summary_text:
        sections.append(("来源摘要", summary_text))
    if content_text:
        sections.append(("来源正文", content_text))
    return sections


def reader_navigation(ids: list[str], index: int, location: str) -> None:
    """Top and bottom navigation share behavior but use distinct widget keys."""
    a, b, c = st.columns([2, 1, 1])
    if a.button("← 返回列表", key=f"reader-{location}-back", use_container_width=True):
        st.session_state.pop("_reader", None)
        # Consuming unread entries changes offsets; resume at the start of that remaining collection.
        if st.session_state.get("_news_filters", {}).get("reading_state") == "unread":
            st.session_state["_news_filters"]["page"] = 1
        st.rerun()
    if b.button(
        "上一篇",
        key=f"reader-{location}-prev",
        disabled=index == 0,
        use_container_width=True,
    ):
        open_article(ids[index - 1], ids)
        st.rerun()
    if c.button(
        "下一篇",
        key=f"reader-{location}-next",
        disabled=index == len(ids) - 1,
        use_container_width=True,
    ):
        open_article(ids[index + 1], ids)
        st.rerun()
    st.caption(f"当前列表第 {index + 1} / {len(ids)} 篇 · 返回列表后可继续翻页")


def render_reader(api, config: dict, names: dict, localtime) -> None:
    reader = st.session_state["_reader"]
    identity, ids = reader["id"], reader["ids"]
    detail = api("articles/" + identity)
    scroll_to_title = not reader["marked"]
    if not reader["marked"]:
        if not detail.get("read_at"):
            changed = api("articles/" + identity + "/reading", "PATCH", {"read": True})
            detail["read_at"] = changed["read_at"]
        reader["marked"] = True
    index = ids.index(identity)
    reader_navigation(ids, index, "top")
    prefs = st.session_state.get("_reading_preferences", {"size": 18, "width": "适中"})
    with st.popover("Aa 阅读设置"):
        size = st.select_slider("正文字号", options=[16, 18, 20, 22], value=prefs["size"])
        width = st.radio(
            "阅读宽度",
            ["紧凑", "适中", "宽松"],
            index=["紧凑", "适中", "宽松"].index(prefs["width"]),
            horizontal=True,
        )
        st.session_state["_reading_preferences"] = {"size": size, "width": width}
    ratios = {"紧凑": [2, 6, 2], "适中": [1, 8, 1], "宽松": [0.1, 9.8, 0.1]}[width]
    _, center, _ = st.columns(ratios)
    with center:
        successful = [
            item for item in detail["analyses"] if item["status"] == "completed" and item["analysis_json"]
        ]
        ai = next((item for item in successful if item["provider"] == "openai-compatible"), None)
        rules = next((item for item in successful if item["provider"] == "rules"), None)
        result = ai["analysis_json"] if ai else {}
        source = names.get(detail["source_id"], "来源")
        minutes = reading_minutes(
            (result.get("summary_zh") or detail["summary"]) + result.get("impact_analysis", "")
        )
        if ai and not config["ai_enabled"]:
            mode_label = "历史 AI 解读"
        elif ai:
            mode_label = "AI 解读"
        elif not config["ai_enabled"]:
            mode_label = "规则/来源模式"
        else:
            mode_label = "暂无 AI 解读"
        st.markdown(
            f'<div class="reader-kicker">来源：{html.escape(source)}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(f'<h1 class="reader-title">{html.escape(detail["title"])}</h1>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="reader-meta"><span class="reader-mode">{html.escape(mode_label)}</span>'
            f"<span>发布 {html.escape(localtime(detail['published_at']))}</span>"
            f"<span>速读约 {minutes} 分钟</span></div>",
            unsafe_allow_html=True,
        )
        st.caption("速读时间按当前摘要/中文速读内容估算，不代表全文阅读时间。")
        a, b, c = st.columns(3)
        a.link_button("打开来源原文 ↗", detail["original_url"], use_container_width=True)
        if b.button("★ 已收藏" if detail.get("bookmarked") else "☆ 收藏这篇", use_container_width=True):
            api(
                "articles/" + identity + "/reading",
                "PATCH",
                {"bookmarked": not detail.get("bookmarked", False)},
            )
            st.rerun()
        if c.button("标为未读" if detail.get("read_at") else "标为已读", use_container_width=True):
            api("articles/" + identity + "/reading", "PATCH", {"read": not bool(detail.get("read_at"))})
            st.rerun()
        if detail["publication_time_inferred"]:
            st.caption("时间说明：来源没有提供可靠发布时间，当前使用发现时间。")
        st.caption(
            "未独立核验 · AI 解读仅基于当前来源，不代表事实已被确认"
            if ai
            else "未独立核验 · 当前仅展示来源/规则信息"
        )
        speed, original, evidence = st.tabs(["中文速读", "原文摘要", "来源与处理"])
        with speed:
            if ai:
                st.markdown('<div class="reader-section">事件概述</div>', unsafe_allow_html=True)
                prose(result.get("summary_zh", ""), size)
                if result.get("key_facts"):
                    st.markdown(
                        '<div class="reader-section">关键事实 · 来源提取</div>', unsafe_allow_html=True
                    )
                    for fact in result["key_facts"]:
                        prose(fact["text"], size, "reader-fact")
                if result.get("impact_analysis"):
                    st.markdown(
                        '<div class="reader-section">潜在影响 · AI 观点</div>', unsafe_allow_html=True
                    )
                    prose(result["impact_analysis"], size)
                if result.get("reasoning_summary"):
                    with st.expander("查看分析依据"):
                        prose(result["reasoning_summary"], size)
                st.markdown('<div class="reader-section">AI 生成信息</div>', unsafe_allow_html=True)
                st.caption(
                    f"模型：{ai['model']} · 生成时间：{localtime(ai['created_at'])}"
                    f" · 估算费用：¥{ai['estimated_cost']:.4f}"
                )
                if not config["ai_enabled"]:
                    st.caption("当前 AI 调用未启用；以上为历史 AI 结果。")
            else:
                if config["ai_enabled"]:
                    st.info("这篇文章还没有中文 AI 解读。下面先展示来源摘要，阅读原文无需等待分析。")
                    prose(detail["summary"] or "来源仅提供标题，请打开来源原文。", size)
                    if st.button("生成中文解读（计入 AI 配额）", key="reader-request-analysis"):
                        task = api("articles/" + identity + "/analyze", "POST")
                        st.session_state["_analysis_requested"] = identity
                        st.success("已加入分析队列。" if task["queued"] else "已有结果或正在处理中。")
                    if st.session_state.get("_analysis_requested") == identity:
                        st.caption("分析受每天配额与月预算限制；额度不足时保留规则结果。")
                        if st.button("刷新这篇文章", key="reader-refresh-analysis"):
                            st.rerun()
                else:
                    st.info("当前为规则/来源模式：未启用 AI 解读，不提供生成入口。下面展示来源摘要。")
                    prose(detail["summary"] or "来源仅提供标题，请打开来源原文。", size)
            st.download_button(
                "保存阅读笔记",
                f"# {detail['title']}\n\n{result.get('summary_zh') or detail['summary']}\n\nAI 潜在影响：{result.get('impact_analysis', '尚未分析')}\n\n来源：{detail['original_url']}\n\n未独立核验",
                file_name="aih-reading-note.md",
                mime="text/markdown",
            )
        with original:
            st.caption("这是订阅源提供的摘要；平台没有抓取或伪造未获授权的文章全文。")
            sections = source_sections(detail.get("summary"), detail.get("content"))
            if not sections:
                prose("来源仅提供标题。", size)
            for title, text in sections:
                st.markdown(f'<div class="reader-section">{title}</div>', unsafe_allow_html=True)
                prose(text, size)
        with evidence:
            st.write("发现时间：", localtime(detail["collected_at"]))
            st.write("原始链接：", detail["original_url"])
            statuses = {
                "completed": "处理完成",
                "budget_degraded": "达到 AI 配额，保留规则结果",
                "ai_retry_wait": "AI 等待重试",
                "pending": "等待处理",
            }
            st.write("处理状态：", statuses.get(detail["processing_status"], detail["processing_status"]))
            if rules:
                with st.expander("规则评分依据"):
                    st.json(rules["analysis_json"].get("score_dimensions", {}))
            with st.expander("原始来源证据"):
                st.json(detail.get("raw_payload"), expanded=False)
        st.divider()
        reader_navigation(ids, index, "bottom")

    if scroll_to_title:
        # Static application code only. Never interpolate external article text into executable HTML.
        st.html(
            """<script>
        requestAnimationFrame(() => requestAnimationFrame(() => {
            const main = document.querySelector('[data-testid="stMain"]');
            if (main) main.scrollTo({top: 0, behavior: 'instant'});
        }));
        </script>""",
            unsafe_allow_javascript=True,
        )


def render_news(api, config: dict, industries: dict, tz, localtime) -> None:
    apply_pending_clear()
    st.markdown(STYLES, unsafe_allow_html=True)
    names = {source["id"]: source["name"] for source in api("sources")}
    if st.session_state.get("_reader"):
        render_reader(api, config, names, localtime)
        return
    saved = st.session_state.get("_news_filters", {})
    initial = {
        "news_q": saved.get("q", ""),
        "news_reading": saved.get("reading_state", "all"),
        "news_sort": saved.get("sort", "collected_at"),
        "news_compact": saved.get("compact", False),
        "news_industry": saved.get("industry_id", ""),
        "news_source": saved.get("source_id", ""),
        "news_language": saved.get("language", ""),
        "news_days": saved.get("days", 0),
        "news_analysis": saved.get("analysis_status", "all"),
    }
    for key, value in initial.items():
        if key not in st.session_state:
            st.session_state[key] = value

    st.session_state["news_q"] = str(st.session_state["news_q"] or "")
    if st.session_state["news_reading"] not in READING_STATES:
        st.session_state["news_reading"] = "all"
    if st.session_state["news_sort"] not in SORT_OPTIONS:
        st.session_state["news_sort"] = "collected_at"
    if not isinstance(st.session_state["news_compact"], bool):
        st.session_state["news_compact"] = False
    if st.session_state["news_industry"] not in ["", *industries]:
        st.session_state["news_industry"] = ""
    if st.session_state["news_source"] not in ["", *names]:
        st.session_state["news_source"] = ""
    if st.session_state["news_language"] not in LANGUAGE_VALUES:
        st.session_state["news_language"] = ""
    if st.session_state["news_days"] not in TIME_VALUES:
        st.session_state["news_days"] = 0
    if st.session_state["news_analysis"] not in ANALYSIS_STATUSES:
        st.session_state["news_analysis"] = "all"

    query = st.text_input(
        "搜索原文 / 中文摘要 / 影响分析",
        key="news_q",
        placeholder="搜索企业、技术、政策或你关心的问题…",
    )
    a, b, c = st.columns([2, 1, 1])
    reading = a.radio(
        "阅读清单",
        READING_STATES,
        key="news_reading",
        format_func=READING_LABELS.get,
        horizontal=True,
    )
    sort = b.selectbox(
        "排序",
        SORT_OPTIONS,
        key="news_sort",
        format_func=SORT_LABELS.get,
    )
    compact = c.toggle("紧凑列表", key="news_compact")
    with st.expander("筛选行业、来源和分析状态", expanded=False):
        a, b, c = st.columns(3)
        industry_values = [""] + list(industries)
        industry = a.selectbox(
            "行业",
            industry_values,
            key="news_industry",
            format_func=lambda value: industries.get(value, "全部行业"),
        )
        source_values = [""] + list(names)
        source = b.selectbox(
            "来源",
            source_values,
            key="news_source",
            format_func=lambda value: names.get(value, "全部来源"),
        )
        language = c.selectbox(
            "语言",
            LANGUAGE_VALUES,
            key="news_language",
            format_func=LANGUAGE_LABELS.get,
        )
        a, b = st.columns(2)
        days = a.selectbox(
            "发布时间范围",
            TIME_VALUES,
            key="news_days",
            format_func=TIME_LABELS.get,
        )
        analysis = b.selectbox(
            "分析状态",
            ANALYSIS_STATUSES,
            key="news_analysis",
            format_func=ANALYSIS_LABELS.get,
        )
    summary_col, clear_col = st.columns([5, 1], vertical_alignment="center")
    summary_col.caption(
        "当前筛选："
        + filter_summary(query, reading, industry, source, language, days, analysis, industries, names)
    )
    clear_col.button(
        "清空筛选",
        key="news_clear",
        on_click=request_clear_filters,
        help=CLEAR_HELP,
        width="stretch",
    )
    filters = {
        "q": query,
        "reading_state": reading,
        "sort": sort,
        "industry_id": industry,
        "source_id": source,
        "language": language,
        "days": days,
        "analysis_status": analysis,
        "compact": compact,
    }
    previous = {key: saved.get(key) for key in filters}
    page_key = "news_page_" + hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()[:16]
    if "_news_target_page" in st.session_state:
        st.session_state[page_key] = st.session_state.pop("_news_target_page")
    if st.session_state.pop("_news_reset_page", False):
        st.session_state[page_key] = 1
    page = st.number_input(
        "页码", min_value=1, value=int(saved.get("page", 1)) if filters == previous else 1, key=page_key
    )
    st.session_state["_news_filters"] = {**filters, "page": page}
    params = {key: value for key, value in filters.items() if key not in {"days", "compact"} and value != ""}
    params.update(limit=20, offset=(page - 1) * 20)
    if days:
        params["start"] = (datetime.now(tz) - timedelta(days=days)).isoformat()
    data = api("articles", params=params)
    pages = max(1, math.ceil(data["total"] / 20))
    if page > pages:
        st.session_state["_news_reset_page"] = True
        st.rerun()
    st.caption(f"{data['total']} 篇文章 · 第 {page} / {pages} 页 · 打开文章后自动保存已读状态")
    st.caption("重要性分值只表示重要程度，不代表可信度；AI 中文摘要与来源摘要均保留未核验标识。")
    if not data["items"]:
        st.info("当前筛选没有匹配文章。可以检查关键词或时间范围，或直接清空筛选。")
        st.button(
            "清空筛选",
            key="news_clear_empty",
            on_click=request_clear_filters,
            help=CLEAR_HELP,
        )
    ids = [item["id"] for item in data["items"]]
    for item in data["items"]:
        with st.container(border=True, key=CARD_KEY_PREFIX + item["id"]):
            st.markdown(
                news_card_html(item, names, industries, localtime, compact=compact),
                unsafe_allow_html=True,
            )
            action, _ = st.columns([1, 2])
            if action.button(
                "开始阅读",
                key="detail_" + item["id"],
                type="primary",
                width="stretch",
            ):
                open_article(item["id"], ids)
                st.rerun()
    a, b, c = st.columns([1, 2, 1])
    if a.button("← 上一页", disabled=page <= 1, use_container_width=True):
        st.session_state["_news_target_page"] = page - 1
        st.rerun()
    b.caption(f"第 {page} / {pages} 页")
    if c.button("下一页 →", disabled=page >= pages, use_container_width=True):
        st.session_state["_news_target_page"] = page + 1
        st.rerun()
