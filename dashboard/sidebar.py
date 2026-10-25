"""Sidebar brand and the single native workspace navigation."""

import streamlit as st

PAGES = ["概览", "实时新闻", "重大事件", "行业管理", "数据源管理", "简报", "系统设置", "运行日志"]
NAV_KEY = "workspace_nav"

SIDEBAR_STYLES = """<style>
[data-testid="stSidebar"] .aih-sidebar-brand {display:flex;align-items:center;gap:.55rem;margin:0 0 .9rem}
[data-testid="stSidebar"] .aih-sidebar-mark {display:inline-flex;align-items:center;justify-content:center;width:1.8rem;height:1.8rem;border:1px solid rgba(98,223,199,.35);border-radius:9px;color:var(--aih-accent);font-size:.95rem;line-height:1}
[data-testid="stSidebar"] .aih-sidebar-brand-title {color:var(--aih-text-strong);font-size:.98rem;font-weight:650;line-height:1.2}
[data-testid="stSidebar"] .aih-sidebar-brand-sub {color:var(--aih-muted);font-size:.62rem;letter-spacing:.12em;line-height:1.3;margin-top:.12rem;text-transform:uppercase}
[data-testid="stSidebar"] [data-testid="stRadio"] [data-testid="stWidgetLabel"] {color:var(--aih-muted);font-size:.76rem;font-weight:600;letter-spacing:.03em;margin-bottom:.15rem}
[data-testid="stSidebar"] [data-testid="stRadioGroup"] {gap:.2rem}
[data-testid="stSidebar"] [data-testid="stRadioOption"] {display:flex;align-items:center;gap:.45rem;width:100%;min-height:2.75rem;padding:.3rem .55rem;margin:0;box-sizing:border-box;border:1px solid transparent;border-radius:9px;color:var(--aih-text);cursor:pointer}
[data-testid="stSidebar"] [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] {color:inherit}
[data-testid="stSidebar"] [data-testid="stRadioOption"] p {margin:0;color:var(--aih-text);font-weight:500}
[data-testid="stSidebar"] [data-testid="stRadioOption"]:hover {background:var(--aih-surface);border-color:var(--aih-border-soft)}
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"],
[data-testid="stSidebar"] [data-testid="stRadioOption"]:has(input:checked) {background:var(--aih-accent-soft);border-color:rgba(98,223,199,.35);box-shadow:inset 3px 0 0 var(--aih-accent)}
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] p,
[data-testid="stSidebar"] [data-testid="stRadioOption"]:has(input:checked) p {color:var(--aih-accent);font-weight:600}
</style>"""

BRAND_HTML = """
<div class="aih-sidebar-brand">
  <span class="aih-sidebar-mark" aria-hidden="true">◈</span>
  <div>
    <div class="aih-sidebar-brand-title">AIH 情报中心</div>
    <div class="aih-sidebar-brand-sub">AI Intelligence Hub</div>
  </div>
</div>
"""


def render_sidebar():
    """Render compact branding, the key input and one native workspace radio."""
    st.sidebar.markdown(SIDEBAR_STYLES, unsafe_allow_html=True)
    st.sidebar.markdown(BRAND_HTML, unsafe_allow_html=True)
    key = st.sidebar.text_input(
        "管理员密钥",
        type="password",
        value="",
        autocomplete="off",
        placeholder="输入后按回车",
    )
    page = st.sidebar.radio("工作空间", PAGES, key=NAV_KEY)
    st.sidebar.caption("所有 AI 分析均为辅助判断。未独立核实的信息标记为 unverified。")
    return key, page
