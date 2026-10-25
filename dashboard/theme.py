"""Small, shared visual foundation for the Streamlit dashboard.

The dashboard is one Streamlit app, so ``Home.py`` applies this stylesheet once
before any page is rendered. These rules use the stable Streamlit test ids that
the app already relies on (``stApp``, ``stSidebar``, ``stMetric``,
``stMainBlockContainer``, ``stMarkdown`` and the button containers). They do not
reach into BaseWeb/component internals, and they leave Plotly charts, dataframes
and the news reader's local reading styles alone.
"""

from __future__ import annotations

import re
from string import Template

import streamlit as st

BACKGROUND = "#0c111b"
PANEL = "#121b2a"
SURFACE = "#172235"
SURFACE_HOVER = "#1d2b41"
BORDER = "#2a3a51"
BORDER_SOFT = "#223047"
TEXT = "#e8eef7"
TEXT_STRONG = "#f4f8fd"
MUTED = "#97a9c0"
ACCENT = "#62dfc7"
ACCENT_STRONG = "#3ecbb2"
FOCUS = "#a7f3e4"

CARD_KEY_PREFIX = "aih-card-"

_CSS = Template(
    """
<style>
:root {
  --aih-bg: $bg;
  --aih-panel: $panel;
  --aih-surface: $surface;
  --aih-surface-hover: $surface_hover;
  --aih-border: $border;
  --aih-border-soft: $border_soft;
  --aih-text: $text;
  --aih-text-strong: $text_strong;
  --aih-muted: $muted;
  --aih-accent: $accent;
  --aih-accent-strong: $accent_strong;
  --aih-accent-soft: rgba(98, 223, 199, 0.14);
  --aih-focus: $focus;
  --aih-radius: 12px;
  --aih-radius-sm: 9px;
  --aih-font: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
    "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif;
}

/* App shell and page rhythm ---------------------------------------------- */
.stApp {
  background: var(--aih-bg);
  color: var(--aih-text);
  font-family: var(--aih-font);
}
[data-testid="stMain"] {
  background: var(--aih-bg);
}
[data-testid="stHeader"] {
  background: transparent;
}
[data-testid="stMainBlockContainer"],
.block-container {
  max-width: 1240px;
  padding: 1.35rem 2rem 3.5rem;
}

/* Type scale: a compact masthead instead of Streamlit's tall default. */
.stApp h1 {
  color: var(--aih-text-strong);
  font-size: clamp(1.65rem, 3vw, 2.05rem);
  line-height: 1.28;
  letter-spacing: -0.022em;
  margin: 0 0 0.35rem;
}
.stApp h2 {
  color: var(--aih-text-strong);
  font-size: clamp(1.22rem, 2vw, 1.45rem);
  line-height: 1.35;
  letter-spacing: -0.015em;
  margin: 1.6rem 0 0.6rem;
}
.stApp h3 {
  color: var(--aih-text-strong);
  font-size: 1.1rem;
  line-height: 1.4;
  letter-spacing: -0.01em;
  margin: 1.35rem 0 0.45rem;
}
[data-testid="stMarkdown"] p {
  line-height: 1.75;
}
[data-testid="stCaptionContainer"] {
  color: var(--aih-muted);
  font-size: 0.82rem;
  line-height: 1.6;
}
[data-testid="stCaptionContainer"] p {
  margin-bottom: 0;
}
[data-testid="stMarkdown"] a,
[data-testid="stMarkdownContainer"] a {
  color: var(--aih-accent);
  text-underline-offset: 2px;
}
[data-testid="stMarkdown"] hr {
  border: 0;
  border-top: 1px solid var(--aih-border);
  margin: 1.5rem 0;
}
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input {
  font-family: inherit;
  caret-color: var(--aih-accent);
}

/* Sidebar ---------------------------------------------------------------- */
[data-testid="stSidebar"] {
  background: var(--aih-panel);
  border-right: 1px solid var(--aih-border-soft);
}
[data-testid="stSidebar"] h1 {
  color: var(--aih-text-strong);
  font-size: 1.12rem;
  letter-spacing: -0.01em;
  margin-bottom: 0.1rem;
}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
  color: var(--aih-muted);
  font-size: 0.74rem;
  line-height: 1.55;
}

/* Cards and forms -------------------------------------------------------- */
[data-testid="stMetric"] {
  background: var(--aih-panel);
  border: 1px solid var(--aih-border);
  border-radius: var(--aih-radius);
  padding: 0.95rem 1.05rem;
}
[data-testid="stMetricLabel"] {
  color: var(--aih-muted);
  font-size: 0.82rem;
}
[data-testid="stMetricValue"] {
  color: var(--aih-text-strong);
  font-size: clamp(1.35rem, 2vw, 1.7rem);
  font-variant-numeric: tabular-nums;
}
[data-testid="stMetricDelta"] {
  font-size: 0.8rem;
}
[data-testid="stVerticalBlock"] {
  border-color: var(--aih-border);
  border-radius: var(--aih-radius);
}
[data-testid="stVerticalBlock"][class*="st-key-aih-card-"] {
  background: var(--aih-panel);
  border: 1px solid var(--aih-border);
  border-radius: var(--aih-radius);
  padding: 1rem 1.1rem;
}
[data-testid="stForm"] {
  background: var(--aih-panel);
  border: 1px solid var(--aih-border);
  border-radius: var(--aih-radius);
  padding: 1rem 1.1rem;
}
[data-testid="stAlertContainer"] {
  border-radius: var(--aih-radius);
}

/* Buttons, links and visible keyboard focus ------------------------------ */
[data-testid="stButton"] button,
[data-testid="stDownloadButton"] button {
  background: var(--aih-surface);
  border: 1px solid var(--aih-border);
  border-radius: var(--aih-radius-sm);
  color: var(--aih-text);
  font-weight: 600;
  transition: background-color 0.16s ease, border-color 0.16s ease, color 0.16s ease;
}
[data-testid="stButton"] button:hover,
[data-testid="stDownloadButton"] button:hover {
  background: var(--aih-surface-hover);
  border-color: var(--aih-accent-strong);
  color: var(--aih-accent);
}
[data-testid="stLinkButton"] a {
  color: var(--aih-accent) !important;
  border-radius: var(--aih-radius-sm);
  font-weight: 600;
}
[data-testid="stButton"] button:focus-visible,
[data-testid="stDownloadButton"] button:focus-visible,
[data-testid="stFormSubmitButton"] button:focus-visible,
[data-testid="stLinkButton"] a:focus-visible,
[data-testid="stTextInput"] input:focus-visible,
[data-testid="stTextArea"] textarea:focus-visible,
[data-testid="stNumberInput"] input:focus-visible,
[data-testid="stRadio"] label:focus-within,
[data-testid="stCheckbox"] label:focus-within {
  outline: 2px solid var(--aih-focus) !important;
  outline-offset: 2px !important;
}

/* Login explanation ------------------------------------------------------ */
.aih-login-eyebrow {
  display: inline-block;
  margin: 0 0 0.85rem;
  padding: 0.28rem 0.65rem;
  border: 1px solid rgba(98, 223, 199, 0.35);
  border-radius: 999px;
  color: var(--aih-accent);
  font-size: 0.72rem;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.aih-login-lede {
  max-width: 46rem;
  margin: 0 0 1.4rem;
  color: var(--aih-muted);
  font-size: 1rem;
  line-height: 1.8;
}
.aih-login-note {
  margin-top: 0.8rem;
  padding: 0.65rem 0.8rem;
  border-left: 2px solid var(--aih-accent-strong);
  border-radius: 0 8px 8px 0;
  background: rgba(98, 223, 199, 0.07);
  color: var(--aih-muted);
  font-size: 0.82rem;
  line-height: 1.7;
}

/* Narrow screens: tighten spacing only; every control stays available. */
@media (max-width: 760px) {
  [data-testid="stMainBlockContainer"],
  .block-container {
    padding: 0.95rem 0.85rem 2.5rem;
  }
  .stApp h1 {
    font-size: 1.55rem;
  }
  [data-testid="stMetric"] {
    padding: 0.8rem 0.85rem;
  }
  [data-testid="stForm"],
  [data-testid="stVerticalBlock"][class*="st-key-aih-card-"] {
    padding: 0.85rem 0.8rem;
  }
  .aih-login-lede {
    font-size: 0.95rem;
  }
}
</style>
"""
)


def stylesheet() -> str:
    """Return the shared stylesheet with tokens substituted in."""
    return _CSS.substitute(
        bg=BACKGROUND,
        panel=PANEL,
        surface=SURFACE,
        surface_hover=SURFACE_HOVER,
        border=BORDER,
        border_soft=BORDER_SOFT,
        text=TEXT,
        text_strong=TEXT_STRONG,
        muted=MUTED,
        accent=ACCENT,
        accent_strong=ACCENT_STRONG,
        focus=FOCUS,
    )


def card_key(name: str) -> str:
    """Build a Streamlit container key that opts a card into the shared style."""
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(name)).strip("-")
    return CARD_KEY_PREFIX + (safe or "card")


def apply_theme() -> None:
    """Inject the shared theme. Call once from Home.py after set_page_config."""
    st.markdown(stylesheet(), unsafe_allow_html=True)
