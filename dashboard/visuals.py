"""Offline world outlines, orbitable 3D heat globe and article analysis charts."""

import html
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import streamlit as st

PALETTE = ["#62dfc7", "#7eafff", "#c09cff", "#ffc575", "#ff8599", "#73c9e8", "#c5d97b", "#bdad96", "#65768e"]
BACKGROUND = "#0c111b"
HEAT_STOPS = [
    (0.0, "#112b48"),
    (0.08, "#173e5b"),
    (0.25, "#176d80"),
    (0.5, "#24b5a1"),
    (0.75, "#f1c26a"),
    (1.0, "#ff815b"),
]
HEAT_SCALE = [[stop, color] for stop, color in HEAT_STOPS]
HEAT_GRADIENT = (
    "linear-gradient(90deg, " + ", ".join(f"{color} {stop * 100:g}%" for stop, color in HEAT_STOPS) + ")"
)


def xyz(lat, lon, radius=1):
    latitude, longitude = np.radians(lat), np.radians(lon)
    return (
        radius * np.cos(latitude) * np.cos(longitude),
        radius * np.cos(latitude) * np.sin(longitude),
        radius * np.sin(latitude),
    )


@lru_cache(maxsize=1)
def borders() -> tuple[list, list, list]:
    x, y, z = [], [], []
    for ring in json.loads((Path(__file__).parent / "assets/world-borders.json").read_text()):
        for lon, lat in ring:
            px, py, pz = xyz(lat, lon, 1.006)
            x.append(float(px))
            y.append(float(py))
            z.append(float(pz))
        x.append(None)
        y.append(None)
        z.append(None)
    return x, y, z


def style(figure: go.Figure, height=320) -> go.Figure:
    figure.update_layout(
        template="plotly_dark",
        paper_bgcolor=BACKGROUND,
        plot_bgcolor=BACKGROUND,
        font={"family": "Arial, sans-serif", "color": "#c8d6e8", "size": 12},
        height=height,
        margin={"l": 12, "r": 20, "t": 18, "b": 24},
        legend={"orientation": "h", "y": -0.18},
    )
    figure.update_xaxes(gridcolor="#1e2a3a", zeroline=False)
    figure.update_yaxes(gridcolor="#1e2a3a", zeroline=False)
    return figure


def heat_legend() -> None:
    """Static legend; only text and the shared gradient, never external content."""
    st.markdown(
        f"""
<div role="img" aria-label="相对热度色阶：从低到高。按当前范围和当前依据内的最大值相对归一化，不是风险等级。"
     style="display:flex;align-items:center;gap:.55rem;margin:.1rem 0 .35rem;color:#97a9c0;font-size:.78rem">
  <span style="color:#dce5f2;font-weight:600">相对热度</span>
  <span>低</span>
  <span aria-hidden="true" style="flex:1;height:8px;border:1px solid #2a3a51;border-radius:999px;background:{HEAT_GRADIENT}"></span>
  <span>高</span>
</div>
""",
        unsafe_allow_html=True,
    )
    st.caption("色阶按当前范围和当前依据内的最大值相对归一化；只表示相对数量，不是风险等级。")


def region_count_rows(regions: list[dict], field: str, mapped: int, total: int) -> list[dict]:
    """Build the accessible fallback table; unknown regions stay out of the map data."""
    active = sorted(
        (region for region in regions if region[field] > 0),
        key=lambda region: (-region[field], region["name"], region["id"]),
    )
    rows = [{"地区": region["name"], "当前依据文章数": region[field]} for region in active]
    rows.append({"地区": "地区未知（未定位）", "当前依据文章数": max(total - mapped, 0)})
    return rows


def globe_figure(regions: list[dict], field: str, revision: str) -> go.Figure:
    active = [region for region in regions if region[field] > 0]
    lat, lon = np.meshgrid(np.linspace(-90, 90, 73), np.linspace(-180, 180, 145), indexing="ij")
    x, y, z = xyz(lat, lon)
    heat = np.zeros_like(x)
    peak = max([region[field] for region in active] or [1])
    for region in active:
        cx, cy, cz = xyz(region["lat"], region["lon"])
        distance = np.arccos(np.clip(x * cx + y * cy + z * cz, -1, 1))
        heat += (region[field] / peak) * np.exp(-((distance / 0.22) ** 2))
    figure = go.Figure(
        go.Surface(
            x=x,
            y=y,
            z=z,
            surfacecolor=np.clip(heat, 0, 1),
            cmin=0,
            cmax=1,
            colorscale=HEAT_SCALE,
            showscale=False,
            hoverinfo="skip",
            lighting={"ambient": 0.85, "diffuse": 0.5, "specular": 0.05},
        )
    )
    bx, by, bz = borders()
    figure.add_trace(
        go.Scatter3d(
            x=bx,
            y=by,
            z=bz,
            mode="lines",
            line={"color": "#6f9bae", "width": 1},
            hoverinfo="skip",
            showlegend=False,
        )
    )
    for latitude in [-60, -30, 0, 30, 60]:
        gx, gy, gz = xyz(np.full(145, latitude), np.linspace(-180, 180, 145), 1.002)
        figure.add_trace(
            go.Scatter3d(
                x=gx,
                y=gy,
                z=gz,
                mode="lines",
                line={"color": "#25465d", "width": 1},
                hoverinfo="skip",
                showlegend=False,
            )
        )
    if active:
        mx, my, mz = xyz(np.array([r["lat"] for r in active]), np.array([r["lon"] for r in active]), 1.025)
        figure.add_trace(
            go.Scatter3d(
                x=mx,
                y=my,
                z=mz,
                mode="markers",
                showlegend=False,
                customdata=[[html.escape(r["name"]), r[field]] for r in active],
                marker={
                    "size": [7 + 16 * np.sqrt(r[field] / peak) for r in active],
                    "color": [r[field] for r in active],
                    "colorscale": [[0, "#68e2d1"], [1, "#ffc575"]],
                    "opacity": 0.95,
                    "line": {"width": 1, "color": "#fff1ca"},
                },
                hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]} 篇文章<extra></extra>",
            )
        )
    focus = max(active, key=lambda region: region[field]) if active else {"lat": 25, "lon": 35}
    cx, cy, cz = xyz(focus["lat"], focus["lon"], 1.6)
    axis = {"visible": False, "range": [-1.1, 1.1], "showbackground": False}
    style(figure, 450)
    figure.update_layout(
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        uirevision=revision,
        scene={
            "xaxis": axis,
            "yaxis": axis,
            "zaxis": axis,
            "aspectmode": "cube",
            "bgcolor": BACKGROUND,
            "dragmode": "orbit",
            "uirevision": revision,
            "camera": {
                "eye": {"x": float(cx), "y": float(cy), "z": float(cz)},
                "projection": {"type": "orthographic"},
            },
        },
    )
    return figure


def trend_figure(timeline: list[dict]) -> go.Figure:
    figure = go.Figure(
        go.Scatter(
            x=[item["time"] for item in timeline],
            y=[item["count"] for item in timeline],
            mode="lines+markers",
            line={"color": PALETTE[0], "width": 3},
            fill="tozeroy",
            fillcolor="rgba(98,223,199,.10)",
            hovertemplate="%{x}<br>新发现 %{y} 篇<extra></extra>",
        )
    )
    # Labels already use the configured local timezone; never let Plotly infer an arbitrary year.
    figure.update_xaxes(type="category")
    figure.update_yaxes(title="文章数", rangemode="tozero")
    return style(figure)


def render_visuals(api) -> None:
    st.subheader("全球情报态势")
    a, b, c = st.columns([1, 1.2, 1], vertical_alignment="bottom")
    days = a.selectbox(
        "分析时间范围",
        [1, 7, 30],
        index=1,
        format_func=lambda n: "最近24小时" if n == 1 else f"最近{n}天",
        key="visual_days",
    )
    mode = b.selectbox(
        "地球热区依据",
        ["mentions", "source_articles"],
        format_func=lambda value: {"mentions": "新闻明确提及的地区", "source_articles": "数据源登记的地区"}[
            value
        ],
        key="globe_basis",
    )
    if c.button("重置地球视角", width="stretch"):
        st.session_state["_globe_revision"] = st.session_state.get("_globe_revision", 0) + 1
    data = api("stats/analytics", params={"days": days})
    st.caption(
        f"按发现时间统计 · {data['timezone']} · 共 {data['total_articles']:,} 篇文章 · 切换范围或点击刷新更新图表，不新增 AI 调用"
    )
    mapped = data["mention_matched_articles"] if mode == "mentions" else data["source_mapped_articles"]
    mode_label = {"mentions": "新闻明确提及的地区", "source_articles": "数据源登记的地区"}[mode]
    active = [region for region in data["regions"] if region[mode] > 0]
    left, right = st.columns([1.35, 1])
    with left:
        st.caption(
            f"全球热点 / {len(active)} 个地区 · {mapped:,} 篇有地区依据 · {data['total_articles'] - mapped:,} 篇地区未知"
        )
        if active:
            heat_legend()
        else:
            if data["total_articles"] == 0:
                reason = "当前时间范围没有文章，因此没有可定位的地区依据。"
            elif mapped == 0:
                reason = "当前范围内的文章没有可定位的地区依据。"
            else:
                reason = "当前依据下没有可定位的地区。"
            st.info(f"{reason}地图不会生成热区，地区未知的报道也不会标注在地图上。")
        st.plotly_chart(
            globe_figure(data["regions"], mode, str(st.session_state.get("_globe_revision", 0)) + mode),
            key="globe_chart",
            width="stretch",
            config={"displaylogo": False, "scrollZoom": True},
        )
        st.caption("拖拽旋转 · 滚轮缩放 · 悬停查看热度。热区为地区示意中心，并非事件精确位置。")
        if mode == "mentions":
            st.caption(
                "以标题和来源摘要中的中英文地名规则匹配；提及不等于事件发生地，一篇文章可涉及多个地区。"
            )
        else:
            st.caption("仅使用来源已登记的地区；不会根据域名猜测，来源所在地不等于新闻发生地。")
        if data["total_articles"]:
            with st.expander("地区数量表（地图不可用/屏幕阅读辅助）", expanded=False):
                multi_region = "同一篇文章可计入多个提及地区；" if mode == "mentions" else ""
                st.caption(
                    f"当前依据：{mode_label}。按文章数降序，同数按地区名称排序；仅列大于 0 的地区。"
                    f"{multi_region}未知地区单列在最后一行，不映射到地图。"
                )
                st.table(
                    region_count_rows(data["regions"], mode, mapped, data["total_articles"]),
                    hide_index=True,
                    alt=f"地区数量表：{mode_label}；地区未知单列，不映射到地图",
                )
    industries = [item for item in data["industries"] if item["count"] > 0]
    color_map = {item["id"]: PALETTE[index % len(PALETTE)] for index, item in enumerate(data["industries"])}
    industries.sort(key=lambda item: item["count"])
    with right:
        st.markdown("#### 行业关注度")
        if industries:
            figure = go.Figure(
                go.Bar(
                    y=[v["name"] for v in industries],
                    x=[v["count"] for v in industries],
                    orientation="h",
                    marker_color=[color_map[item["id"]] for item in industries],
                    text=[v["count"] for v in industries],
                    textposition="outside",
                    hovertemplate="%{y} · %{x} 篇<extra></extra>",
                )
            )
            figure.update_layout(xaxis_title="文章数", yaxis={"automargin": True})
            figure.update_xaxes(range=[0, max(item["count"] for item in industries) * 1.2])
            st.plotly_chart(
                style(figure, 420), key="industry_chart", width="stretch", config={"displayModeBar": False}
            )
        else:
            st.info("此时间范围尚无文章，采集后自动生成分析。")
        st.caption("按文章分类统计；一篇文章可属于多个行业，行业数量合计可能大于文章总数。")
    if active and mode == "mentions":
        names = {region["id"]: f"{region['name']} · {region['mentions']}篇" for region in active}
        chosen = st.selectbox("查看地区对应报道", list(names), format_func=names.get, key="region_examples")
        region = next(region for region in active if region["id"] == chosen)
        with st.expander("查看匹配依据与最近报道", expanded=False):
            for sample in region["samples"]:
                st.write(sample["title"])
                st.caption(f"{sample['source']} · 文本命中“{sample['matched_term']}”")
                st.link_button("阅读来源", sample["url"], key="region_" + sample["article_id"])
    if not data["total_articles"]:
        return
    trend, severity = st.columns([1.35, 1])
    with trend:
        st.markdown("#### 信息发现趋势")
        figure = trend_figure(data["timeline"])
        st.plotly_chart(style(figure), key="volume_chart", width="stretch", config={"displayModeBar": False})
    with severity:
        st.markdown("#### 重要性分布")
        thresholds = data["thresholds"]
        labels = [
            f"重大 ≥{thresholds['critical']}",
            f"重要 {thresholds['important']}–{thresholds['critical'] - 1}",
            f"普通 <{thresholds['important']}",
            "尚未评分",
        ]
        figure = go.Figure(
            go.Pie(
                labels=labels,
                values=[v["count"] for v in data["importance"]],
                hole=0.68,
                sort=False,
                marker_colors=["#ff8599", "#ffc575", PALETTE[0], "#65768e"],
                textinfo="percent",
                hovertemplate="%{label}<br>%{value} 篇<extra></extra>",
            )
        )
        figure.update_layout(
            annotations=[
                {
                    "text": str(data["total_articles"]),
                    "x": 0.5,
                    "y": 0.5,
                    "font": {"size": 28},
                    "showarrow": False,
                }
            ]
        )
        st.plotly_chart(
            style(figure), key="importance_chart", width="stretch", config={"displayModeBar": False}
        )
        st.caption("按系统评分阈值统计，不代表事实核实程度。")
    with st.expander("展开行业变化与来源构成", expanded=False):
        if industries:
            heat = go.Figure(
                go.Heatmap(
                    x=[v["time"] for v in data["timeline"]],
                    y=[v["name"] for v in industries],
                    z=[v["timeline"] for v in industries],
                    colorscale=[[0, "#132238"], [1, PALETTE[0]]],
                    hovertemplate="%{y}<br>%{x} · %{z} 篇<extra></extra>",
                    colorbar={"title": "篇"},
                )
            )
            heat.update_xaxes(type="category")
            st.plotly_chart(
                style(heat, 340), key="industry_heat_chart", width="stretch", config={"displayModeBar": False}
            )
        source_col, language_col = st.columns(2)
        with source_col:
            st.markdown("#### 来源贡献 · 前8名")
            rows = list(reversed(data["sources"][:8]))
            figure = go.Figure(
                go.Bar(
                    y=[v["name"] for v in rows],
                    x=[v["count"] for v in rows],
                    orientation="h",
                    marker_color=PALETTE[1],
                    hovertemplate="%{y} · %{x} 篇<extra></extra>",
                )
            )
            figure.update_yaxes(automargin=True)
            st.plotly_chart(
                style(figure), key="source_chart", width="stretch", config={"displayModeBar": False}
            )
        with language_col:
            st.markdown("#### 新闻语言")
            figure = go.Figure(
                go.Pie(
                    labels=[v["name"] for v in data["languages"]],
                    values=[v["count"] for v in data["languages"]],
                    hole=0.55,
                    marker_colors=PALETTE,
                )
            )
            st.plotly_chart(
                style(figure), key="language_chart", width="stretch", config={"displayModeBar": False}
            )
    st.caption("地球轮廓：Natural Earth · 本地公共领域底图，无需地图密钥。热区光晕仅为提及频次的可视化示意。")
