"""Sidebar brand, credentials, session state and the workspace navigation."""

import streamlit as st

PAGES = ["概览", "实时新闻", "重大事件", "行业管理", "数据源管理", "简报", "系统设置", "运行日志"]
NAV_KEY = "workspace_nav"
ADMIN_KEY = "admin_key"
LOGOUT_KEY = "logout_session"
SENSITIVE_CACHE_KEYS = ("_reader", "_analysis_requested", "_reading_preferences", "_custom_brief")

SIDEBAR_STYLES = """<style>
[data-testid="stSidebar"] .aih-sidebar-brand {display:flex;align-items:center;gap:.55rem;margin:0 0 .9rem}
[data-testid="stSidebar"] .aih-sidebar-mark {display:inline-flex;align-items:center;justify-content:center;width:1.8rem;height:1.8rem;border:1px solid rgba(98,223,199,.35);border-radius:9px;color:var(--aih-accent);font-size:.95rem;line-height:1}
[data-testid="stSidebar"] .aih-sidebar-brand-title {color:var(--aih-text-strong);font-size:.98rem;font-weight:650;line-height:1.2}
[data-testid="stSidebar"] .aih-sidebar-brand-sub {color:var(--aih-muted);font-size:.62rem;letter-spacing:.12em;line-height:1.3;margin-top:.12rem;text-transform:uppercase}
[data-testid="stSidebar"] .aih-session-ok {color:var(--aih-accent);font-size:.78rem;font-weight:600;line-height:1.4;margin:.1rem 0 .35rem}
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


def _bump_auth_version() -> None:
    st.session_state["_sidebar_auth_version"] = int(st.session_state.get("_sidebar_auth_version", 0)) + 1


def clear_verified() -> None:
    """Drop the current connection verification without touching the key or caches."""
    if st.session_state.get("_sidebar_verified"):
        st.session_state["_sidebar_verified"] = False
        _bump_auth_version()
        st.session_state["_sidebar_credentials_expanded"] = True


def _key_changed() -> None:
    clear_verified()


def _logout() -> None:
    """Clear local credentials and temporary reader state only."""
    st.session_state[ADMIN_KEY] = ""
    st.session_state["_sidebar_verified"] = False
    _bump_auth_version()
    st.session_state["_sidebar_credentials_expanded"] = True
    for cache_key in SENSITIVE_CACHE_KEYS:
        st.session_state.pop(cache_key, None)


def mark_verified(status) -> None:
    """Fill the reserved status slot only after a successful GET settings."""
    status.markdown(
        '<div class="aih-session-ok">● 本次连接已验证</div>',
        unsafe_allow_html=True,
    )
    if not st.session_state.get("_sidebar_verified"):
        st.session_state["_sidebar_verified"] = True
        _bump_auth_version()


def render_sidebar():
    """Render branding, one workspace radio and collapsible credentials."""
    st.sidebar.markdown(SIDEBAR_STYLES, unsafe_allow_html=True)
    st.sidebar.markdown(BRAND_HTML, unsafe_allow_html=True)
    page = st.sidebar.radio("工作空间", PAGES, key=NAV_KEY)

    verified = bool(st.session_state.get("_sidebar_verified"))
    auth_version = int(st.session_state.get("_sidebar_auth_version", 0))
    if st.session_state.get("_sidebar_expander_version") != auth_version:
        st.session_state["_sidebar_credentials_expanded"] = not verified
        st.session_state["_sidebar_expander_version"] = auth_version
    expanded = bool(st.session_state.get("_sidebar_credentials_expanded", not verified))

    status = st.sidebar.empty()
    with st.sidebar.expander("会话与凭据", expanded=expanded):
        key = st.text_input(
            "管理员密钥",
            type="password",
            value="",
            autocomplete="off",
            placeholder="输入后按回车",
            key=ADMIN_KEY,
            on_change=_key_changed,
        )
        st.button(
            "退出当前会话",
            key=LOGOUT_KEY,
            on_click=_logout,
            disabled=not bool(key),
            width="stretch",
        )
    if not key:
        clear_verified()
    st.sidebar.caption("所有 AI 分析均为辅助判断。未独立核实的信息标记为 unverified。")
    return key, page, status
