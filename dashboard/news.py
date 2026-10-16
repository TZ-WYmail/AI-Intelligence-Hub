"""News browsing and a persistent, full-page reading workspace."""

import hashlib
import html
import json
import math
import re
from datetime import datetime, timedelta

import streamlit as st

STYLES = """<style>
.news-title {font-size:1.23rem;font-weight:600;line-height:1.5;margin:8px 0 10px;overflow-wrap:anywhere;color:#e9eef7}
.news-excerpt {font-size:.96rem;line-height:1.85;color:#bcc9da;overflow-wrap:anywhere;white-space:pre-wrap}
.news-meta {font-size:.78rem;color:#91a4bc;line-height:1.7}
.reader-title {font-size:clamp(1.6rem,3vw,2.3rem);font-weight:650;line-height:1.45;margin:18px 0;color:#f1f5fc;overflow-wrap:anywhere}
.reading-prose {line-height:1.95;color:#dce5f2;white-space:pre-wrap;overflow-wrap:anywhere;margin:12px 0 22px}
.reader-section {font-size:1.08rem;font-weight:650;letter-spacing:.02em;margin:26px 0 10px;color:#71d9c2}
.reader-fact {padding:12px 18px;margin:12px 0;border-left:3px solid #4fb6a1;background:#13232d;border-radius:0 8px 8px 0}
[data-testid=stMainBlockContainer]{max-width:1240px;padding-bottom:4rem}
</style>"""


def snippet(text: str, length: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= length:
        return text
    return text[:length].rstrip() + "…"


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
    a, b, c = st.columns([2, 1, 1])
    if a.button("← 返回列表", use_container_width=True):
        st.session_state.pop("_reader", None)
        # Consuming unread entries changes offsets; resume at the start of that remaining collection.
        if st.session_state.get("_news_filters", {}).get("reading_state") == "unread":
            st.session_state["_news_filters"]["page"] = 1
        st.rerun()
    if b.button("上一篇", disabled=index == 0, use_container_width=True):
        open_article(ids[index - 1], ids)
        st.rerun()
    if c.button("下一篇", disabled=index == len(ids) - 1, use_container_width=True):
        open_article(ids[index + 1], ids)
        st.rerun()
    st.caption(f"当前列表第 {index + 1} / {len(ids)} 篇 · 返回列表后可继续翻页")
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
        st.markdown(f'<div class="reader-title">{html.escape(detail["title"])}</div>', unsafe_allow_html=True)
        successful = [
            item for item in detail["analyses"] if item["status"] == "completed" and item["analysis_json"]
        ]
        ai = next((item for item in successful if item["provider"] == "openai-compatible"), None)
        rules = next((item for item in successful if item["provider"] == "rules"), None)
        result = ai["analysis_json"] if ai else {}
        minutes = reading_minutes(
            (result.get("summary_zh") or detail["summary"]) + result.get("impact_analysis", "")
        )
        st.caption(
            f"{names.get(detail['source_id'], '来源')} · 发布 {localtime(detail['published_at'])} · 约 {minutes} 分钟阅读"
        )
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
        st.caption("未独立核验 · AI 解读仅基于当前来源，不代表事实已被确认")
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
                        st.write(result["reasoning_summary"])
                st.caption(
                    f"{ai['model']} · 生成 {localtime(ai['created_at'])} · 估算费用 ¥{ai['estimated_cost']:.4f}"
                )
            else:
                st.info("这篇文章还没有中文 AI 解读。下面先展示来源摘要，阅读原文无需等待分析。")
                prose(detail["summary"] or "来源仅提供标题，请打开来源原文。", size)
                if config["ai_enabled"] and st.button("生成中文解读（计入 AI 配额）"):
                    task = api("articles/" + identity + "/analyze", "POST")
                    st.session_state["_analysis_requested"] = identity
                    st.success("已加入分析队列。" if task["queued"] else "已有结果或正在处理中。")
                if st.session_state.get("_analysis_requested") == identity:
                    st.caption("分析受每天配额与月预算限制；额度不足时保留规则结果。")
                    if st.button("刷新这篇文章"):
                        st.rerun()
            st.download_button(
                "保存阅读笔记",
                f"# {detail['title']}\n\n{result.get('summary_zh') or detail['summary']}\n\nAI 潜在影响：{result.get('impact_analysis', '尚未分析')}\n\n来源：{detail['original_url']}\n\n未独立核验",
                file_name="aih-reading-note.md",
                mime="text/markdown",
            )
        with original:
            st.caption("这是订阅源提供的摘要；平台没有抓取或伪造未获授权的文章全文。")
            prose(detail["summary"] or "来源仅提供标题。", size)
            if detail.get("content"):
                prose(detail["content"], size)
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
    st.markdown(STYLES, unsafe_allow_html=True)
    names = {source["id"]: source["name"] for source in api("sources")}
    if st.session_state.get("_reader"):
        render_reader(api, config, names, localtime)
        return
    saved = st.session_state.get("_news_filters", {})
    query = st.text_input(
        "搜索原文 / 中文摘要 / 影响分析",
        value=saved.get("q", ""),
        placeholder="搜索企业、技术、政策或你关心的问题…",
    )
    a, b, c = st.columns([2, 1, 1])
    collections = ["all", "unread", "saved", "read"]
    reading = a.radio(
        "阅读清单",
        collections,
        index=collections.index(saved.get("reading_state", "all")),
        format_func=lambda value: {"all": "全部", "unread": "未读", "saved": "我的收藏", "read": "已读"}[
            value
        ],
        horizontal=True,
    )
    sort_options = ["collected_at", "published_at", "importance_score"]
    sort = b.selectbox(
        "排序",
        sort_options,
        index=sort_options.index(saved.get("sort", "collected_at")),
        format_func=lambda value: {
            "collected_at": "最新发现",
            "published_at": "最新发布",
            "importance_score": "重要优先",
        }[value],
    )
    compact = c.toggle("紧凑列表", value=saved.get("compact", False))
    with st.expander("筛选行业、来源和分析状态", expanded=False):
        a, b, c = st.columns(3)
        industry_values = [""] + list(industries)
        industry = a.selectbox(
            "行业",
            industry_values,
            index=industry_values.index(saved.get("industry_id", ""))
            if saved.get("industry_id", "") in industry_values
            else 0,
            format_func=lambda value: industries.get(value, "全部行业"),
        )
        source_values = [""] + list(names)
        source = b.selectbox(
            "来源",
            source_values,
            index=source_values.index(saved.get("source_id", ""))
            if saved.get("source_id", "") in source_values
            else 0,
            format_func=lambda value: names.get(value, "全部来源"),
        )
        langs = ["", "zh", "en"]
        language = c.selectbox(
            "语言",
            langs,
            index=langs.index(saved.get("language", "")),
            format_func=lambda value: {"": "全部语言", "zh": "中文", "en": "英文"}[value],
        )
        a, b = st.columns(2)
        days = a.selectbox(
            "发布时间范围",
            [0, 1, 7, 30],
            index=[0, 1, 7, 30].index(saved.get("days", 0)),
            format_func=lambda value: "全部时间" if value == 0 else f"最近 {value} 天",
        )
        statuses = ["all", "ai", "rules", "failed"]
        analysis = b.selectbox(
            "分析状态",
            statuses,
            index=statuses.index(saved.get("analysis_status", "all")),
            format_func=lambda value: {
                "all": "全部",
                "ai": "已有 AI 分析",
                "rules": "暂无 AI 分析",
                "failed": "AI 等待重试",
            }[value],
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
    if not data["items"]:
        st.info("当前清单没有文章。可以切换清单或调整筛选条件。")
    ids = [item["id"] for item in data["items"]]
    for item in data["items"]:
        with st.container(border=True):
            markers = ("★ 收藏 · " if item.get("bookmarked") else "") + (
                "已读" if item.get("read_at") else "未读"
            )
            labels = " / ".join(industries.get(value, value) for value in item["industry_ids"])
            st.markdown(
                f'<div class="news-meta">{html.escape(markers)} · {item["importance_score"]} 分 · {html.escape(names.get(item["source_id"], "来源"))} · {localtime(item["published_at"])} · {html.escape(labels)}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(f'<div class="news-title">{html.escape(item["title"])}</div>', unsafe_allow_html=True)
            if not compact:
                text = item.get("ai_summary_zh") or item["summary"]
                st.markdown(
                    f'<div class="news-excerpt">{html.escape(snippet(text, 220) or "来源仅提供标题，阅读原文了解详情。")}</div>',
                    unsafe_allow_html=True,
                )
            a, b = st.columns([2, 1])
            if a.button("开始阅读", key="detail_" + item["id"]):
                open_article(item["id"], ids)
                st.rerun()
            b.caption("AI 中文摘要 · 未核验" if item.get("ai_summary_zh") else "来源摘要")
    a, b, c = st.columns([1, 2, 1])
    if a.button("← 上一页", disabled=page <= 1, use_container_width=True):
        st.session_state["_news_target_page"] = page - 1
        st.rerun()
    b.caption(f"第 {page} / {pages} 页")
    if c.button("下一页 →", disabled=page >= pages, use_container_width=True):
        st.session_state["_news_target_page"] = page + 1
        st.rerun()
