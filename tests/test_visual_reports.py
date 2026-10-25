import html
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from streamlit.testing.v1 import AppTest

from app.models import Article, Digest, Industry, Source, now
from app.services.analytics import mentioned_regions
from dashboard.briefs import split_report
from dashboard.visuals import globe_figure, horizontal_count_figure, language_label, trend_figure


def _visual_data(**overrides):
    data = {
        "timezone": "Asia/Shanghai",
        "total_articles": 3,
        "mention_matched_articles": 0,
        "source_mapped_articles": 0,
        "regions": [
            {
                "id": "CN",
                "name": "中国",
                "lat": 35,
                "lon": 104,
                "mentions": 0,
                "source_articles": 0,
                "samples": [],
            }
        ],
        "industries": [{"id": "ai", "name": "AI", "count": 3, "timeline": [3]}],
        "timeline": [{"time": "10-25", "count": 3}],
        "importance": [
            {"id": "critical", "count": 0},
            {"id": "important", "count": 1},
            {"id": "ordinary", "count": 2},
            {"id": "unscored", "count": 0},
        ],
        "thresholds": {"critical": 85, "important": 60},
        "sources": [{"name": "Fixture", "count": 3}],
        "languages": [{"name": "en", "count": 3}],
    }
    data.update(overrides)
    return data


def _run_visuals(data):
    code = (
        "import streamlit as st\n"
        "from dashboard.visuals import render_visuals\n"
        f"DATA = {data!r}\n"
        "def api(path, params=None):\n"
        "    return DATA\n"
        "render_visuals(api)\n"
    )
    return AppTest.from_string(code, default_timeout=20).run()


def test_region_matching_requires_text_and_handles_nested_names():
    assert dict(mentioned_regions("China and 中国 work with United States")) == {
        "US": "United States",
        "CN": "China",
    }
    assert mentioned_regions("NVIDIA and NASA help us build better chips in Chinatown.") == []
    assert dict(mentioned_regions("印度尼西亚与印度、台湾地区开展合作")) == {
        "IN": "印度",
        "ID": "印度尼西亚",
        "TW": "台湾",
    }
    assert dict(mentioned_regions("印度尼西亚开展合作")) == {"ID": "印度尼西亚"}


def test_language_labels_map_common_codes_without_guessing_unknown():
    assert language_label("zh") == "中文"
    assert language_label("EN") == "英文"
    assert language_label("zh-CN") == "中文"
    assert language_label("unknown") == "未知"
    assert language_label("xx-custom") == "xx-custom"
    assert language_label("") == "未知"


def test_same_prefix_long_names_keep_separate_bars_and_full_names_traceable():
    first = "Shared<b>PrefixAlpha"
    second = "Shared<b>PrefixBeta"
    figure = horizontal_count_figure([first, second], [7, 3], ["#111111", "#222222"])
    assert list(figure.data[0].y) == [0, 1]
    assert list(figure.data[0].x) == [7, 3]
    assert list(figure.layout.yaxis.tickvals) == [0, 1]
    assert figure.layout.yaxis.ticktext[0] == figure.layout.yaxis.ticktext[1]
    assert "&lt;b&gt;" in figure.layout.yaxis.ticktext[0]
    assert list(figure.data[0].customdata[0]) == [html.escape(first)]
    assert list(figure.data[0].customdata[1]) == [html.escape(second)]
    assert "%{customdata[0]}" in figure.data[0].hovertemplate


def test_analytics_time_window_multicategory_and_unmapped(news_client, db):
    rows = list(db.scalars(select(Article).order_by(Article.id)))
    db.add(Industry(id="energy", name="Energy", slug="energy"))
    rows[0].title, rows[0].summary = "China and United States announce cooperation", ""
    rows[0].industry_ids = ["ai", "energy", "ai"]
    rows[1].title, rows[1].summary = "News without regional evidence", ""
    rows[1].collected_at = now() - timedelta(days=8)
    db.get(Source, "source").country = "us"
    db.commit()
    data = news_client.get("/api/v1/stats/analytics?days=7").json()
    assert data["total_articles"] == 1 and data["mention_matched_articles"] == 1
    assert data["source_mapped_articles"] == 1 and data["mention_unknown_articles"] == 0
    assert {item["id"]: item["count"] for item in data["industries"]} == {
        "ai": 1,
        "energy": 1,
        "uncategorized": 0,
    }
    assert sum(item["count"] for item in data["timeline"]) == 1
    assert sum(item["count"] for item in data["importance"]) == 1
    cn = next(item for item in data["regions"] if item["id"] == "CN")
    assert cn["mentions"] == 1 and cn["samples"][0]["matched_term"] == "China"
    assert cn["samples"][0]["article_id"] == rows[0].id
    data = news_client.get("/api/v1/stats/analytics?days=30").json()
    assert data["total_articles"] == 2 and data["mention_unknown_articles"] == 1
    assert sum(item["count"] for item in data["timeline"]) == 2
    assert news_client.get("/api/v1/stats/analytics?days=31").status_code == 422
    assert news_client.get("/api/v1/stats/analytics", headers={"X-API-Key": "wrong"}).status_code == 401


def test_globe_works_with_no_regions_and_preserves_camera_revision():
    blank = globe_figure([], "mentions", "stable")
    assert blank.layout.scene.dragmode == "orbit"
    assert blank.layout.uirevision == "stable"
    assert len(blank.data[1].x) > 5000
    figure = globe_figure([{"name": "中国", "mentions": 5, "lat": 35, "lon": 104}], "mentions", "reset")
    assert figure.data[-1].customdata[0][1] == 5
    assert "http" not in figure.to_json()
    trend = trend_figure([{"time": "12-31", "count": 1}, {"time": "01-01", "count": 2}])
    assert trend.layout.xaxis.type == "category"
    assert list(trend.data[0].x) == ["12-31", "01-01"]


def test_globe_without_evidence_explains_gap_and_keeps_unknown_out_of_map_data():
    ui = _run_visuals(_visual_data())
    assert not ui.exception
    assert any("没有可定位的地区依据" in item.value for item in ui.info)
    table = ui.table[0].value
    assert table["地区"].tolist() == ["地区未知（未定位）"]
    assert table["当前依据文章数"].tolist() == [3]
    assert not any("相对热度" in item.value for item in ui.markdown)

    ui = _run_visuals(
        _visual_data(
            total_articles=4,
            mention_matched_articles=3,
            regions=[
                {
                    "id": "B",
                    "name": "Beta",
                    "lat": 10,
                    "lon": 10,
                    "mentions": 2,
                    "source_articles": 0,
                    "samples": [],
                },
                {
                    "id": "A",
                    "name": "Alpha",
                    "lat": 20,
                    "lon": 20,
                    "mentions": 1,
                    "source_articles": 0,
                    "samples": [],
                },
                {
                    "id": "C",
                    "name": "Gamma",
                    "lat": 30,
                    "lon": 30,
                    "mentions": 1,
                    "source_articles": 0,
                    "samples": [],
                },
                {
                    "id": "D",
                    "name": "Delta",
                    "lat": 40,
                    "lon": 40,
                    "mentions": 0,
                    "source_articles": 0,
                    "samples": [],
                },
            ],
        )
    )
    assert not ui.exception
    assert any("相对热度" in item.value for item in ui.markdown)
    assert any("不是风险等级" in item.value for item in ui.caption)
    table = ui.table[0].value
    assert table["地区"].tolist() == ["Beta", "Alpha", "Gamma", "地区未知（未定位）"]
    assert table["当前依据文章数"].tolist() == [2, 1, 1, 1]


def test_split_daily_and_custom_reports_without_losing_entries():
    prefix = "# Report\n\nDates and context\n\n"
    entries = [f"## Title {i}\n\nSummary {i}\n\nSource\n" for i in range(12)]
    header, parts = split_report(prefix + "\n".join(entries))
    assert "Dates and context" in header and len(parts) == 12
    assert "Summary 11" in parts[-1]
    assert (
        len(
            split_report(
                prefix + "- [A](https://example.com)\n  Summary A\n- [B](https://example.com/b)\n  Summary B"
            )[1]
        )
        == 2
    )
    assert split_report("No articles")[1] == []


def test_report_ui_pages_preserve_custom_results_and_reset_sizes(news_client, db):
    markdown = "# 每日简报\n\n时间范围\n\n" + "\n".join(
        f"## Entry {i:02}\n\n85 分 · AI · 未独立核验\n\nUnique summary {i:02}\n" for i in range(12)
    )
    digest = Digest(
        title="Fixture brief",
        period_start=now() - timedelta(days=1),
        period_end=now(),
        article_count=927,
        markdown=markdown,
    )
    db.add(digest)
    db.commit()
    custom = "# 自定义简报\n\n" + "\n".join(
        f"- [Custom {i:02}](https://example.com/{i}) · 85分 · unverified\n  Custom summary {i:02}"
        for i in range(12)
    )

    def request(method, url, **kwargs):
        if method == "POST" and urlsplit(url).path == "/api/v1/reports":
            return httpx.Response(200, json={"markdown": custom, "total": 12})
        return news_client.request(
            method,
            urlsplit(url).path,
            headers=kwargs.get("headers"),
            json=kwargs.get("json"),
            params=kwargs.get("params"),
        )

    with patch("httpx.request", request):
        ui = AppTest.from_file(str(Path("dashboard/Home.py").resolve()), default_timeout=30).run()
        ui.text_input[0].input("test-admin").run()
        ui.sidebar.radio[0].set_value("简报").run()
        assert not ui.exception
        assert any("统计区间" in item.value for item in ui.caption)
        assert any("结束时刻不含" in item.value for item in ui.caption)
        assert any("生成时间" in item.value for item in ui.caption)
        assert any("条目预览未经独立核验" in item.value for item in ui.caption)
        assert any("统计范围内文章总量：927 篇 · 入选情报：12 条" in item.value for item in ui.caption)
        assert any(expander.label == "查看原始报告时间范围与说明" for expander in ui.expander)
        assert any("Entry 00" in item.value for item in ui.markdown)
        assert not any("Entry 05" in item.value for item in ui.markdown)
        page_text = "\n".join(item.value for item in ui.markdown)
        assert page_text.count("Unique summary 00") == 2
        assert "未独立核验" in page_text
        ui.button(key="daily_brief_next").click().run()
        assert any("Entry 05" in item.value for item in ui.markdown)
        assert not any("Entry 00" in item.value for item in ui.markdown)
        ui.selectbox(key="daily_brief_size").set_value(10).run()
        assert ui.number_input(key="daily_brief_page").value == 1
        assert any("Entry 09" in item.value for item in ui.markdown)
        ui.button(key="daily_brief_bottom_next").click().run()
        assert ui.number_input(key="daily_brief_page").value == 2
        assert any("Entry 10" in item.value for item in ui.markdown)
        assert not any("Entry 00" in item.value for item in ui.markdown)
        ui.button(key="daily_brief_bottom_previous").click().run()
        assert ui.number_input(key="daily_brief_page").value == 1
        assert any("Entry 00" in item.value for item in ui.markdown)
        next(button for button in ui.button if button.label == "生成 Markdown 简报").click().run()
        assert not ui.exception
        custom_header = next(item for item in ui.expander if item.label == "自定义时间范围")
        assert any("入选情报：12 条" in item.value for item in custom_header.caption)
        assert not any("统计范围内文章总量" in item.value for item in custom_header.caption)
        assert any("Custom 00" in item.value for item in ui.markdown)
        custom_text = "\n".join(item.value for item in ui.markdown)
        assert custom_text.count("Custom summary 00") == 2
        assert "https://example.com/0" in custom_text and "unverified" in custom_text
        ui.button(key="custom_brief_next").click().run()
        assert any("Custom 05" in item.value for item in ui.markdown)
        assert not any("Custom 00" in item.value for item in ui.markdown)
        ui.button(key="custom_brief_next").click().run()
        assert any("Custom 11" in item.value for item in ui.markdown)
        assert ui.button(key="custom_brief_next").disabled
        assert ui.button(key="custom_brief_bottom_next").disabled


def test_briefs_defang_markdown_images_but_keep_raw_download(news_client, db):
    markdown = (
        "# 每日简报\n\n"
        "![remote3](https://evil.example/header.png) 说明\n\n"
        "[track]: https://evil.example/ref.png\n\n"
        "## Title ![remote2](https://evil.example/title.png)\n\n"
        "85 分 · AI · 未独立核验\n\n"
        "Safe summary ![remote](https://evil.example/x.png) ![ref][track] "
        "\\![escaped](https://evil.example/esc.png) and [source](https://example.com/ok).\n"
    )
    digest = Digest(
        title="Report ![title](https://evil.example/title.png)",
        period_start=now() - timedelta(days=1),
        period_end=now(),
        article_count=1,
        markdown=markdown,
    )
    db.add(digest)
    db.commit()

    def request(method, url, **kwargs):
        return news_client.request(
            method,
            urlsplit(url).path,
            headers=kwargs.get("headers"),
            json=kwargs.get("json"),
            params=kwargs.get("params"),
        )

    with patch("httpx.request", request), patch("dashboard.briefs.st.download_button") as download:
        ui = AppTest.from_file(str(Path("dashboard/Home.py").resolve()), default_timeout=30).run()
        ui.text_input[0].input("test-admin").run()
        ui.sidebar.radio[0].set_value("简报").run()
        assert not ui.exception

    displayed = "\n".join(item.value for item in ui.markdown)
    subheaders = "\n".join(item.value for item in ui.subheader)
    assert "Safe summary" in displayed and "https://example.com/ok" in displayed
    assert "![" not in displayed and "![" not in subheaders
    assert "!\\[" in displayed and "!\\[" in subheaders
    args, _ = download.call_args
    assert args[1] == markdown
    assert "![remote]" in args[1] and "[track]:" in args[1] and "\\![escaped]" in args[1]
