from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from sqlalchemy import select
from streamlit.testing.v1 import AppTest

from app.models import Article, now


def test_news_filter_summary_and_clear_preserve_reader_preferences(news_client, db):
    source_article = db.scalar(select(Article))
    for index in range(25):
        db.add(
            Article(
                source_id=source_article.source_id,
                external_id=f"extra-filter-{index}",
                title=f"Extra article {index}",
                summary="Readable summary",
                original_url=f"https://example.com/filter/{index}",
                canonical_url=f"https://example.com/filter/{index}",
                content_hash=f"extra-filter-{index}",
                published_at=now(),
                collected_at=now(),
                language="en",
                industry_ids=["ai"],
            )
        )
    db.commit()

    calls = []

    def request(method, url, **kwargs):
        calls.append((method, urlsplit(url).path))
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
        ui.sidebar.radio[0].set_value("实时新闻").run()
        assert not ui.exception

        next(button for button in ui.button if button.label == "开始阅读").click().run()
        assert not ui.exception
        ui.select_slider[0].set_value(22).run()
        next(radio for radio in ui.radio if radio.label == "阅读宽度").set_value("宽松").run()
        next(button for button in ui.button if button.label == "☆ 收藏这篇").click().run()
        next(button for button in ui.button if button.label == "← 返回列表").click().run()
        assert not ui.exception

        ui.selectbox(key="news_sort").set_value("published_at").run()
        ui.toggle(key="news_compact").set_value(True).run()
        ui.text_input(key="news_q").input("Extra").run()
        ui.selectbox(key="news_industry").set_value("ai").run()
        ui.selectbox(key="news_source").set_value("source").run()
        ui.selectbox(key="news_language").set_value("en").run()
        ui.selectbox(key="news_days").set_value(7).run()
        ui.selectbox(key="news_analysis").set_value("rules").run()
        next(radio for radio in ui.radio if radio.label == "阅读清单").set_value("unread").run()
        assert not ui.exception

        summary = next(caption.value for caption in ui.caption if caption.value.startswith("当前筛选："))
        assert "关键词：「Extra」" in summary
        assert "清单：未读" in summary
        assert "行业：AI" in summary
        assert "来源：Fixture" in summary
        assert "语言：英文" in summary
        assert "时间：最近 7 天" in summary
        assert "分析：暂无 AI 分析" in summary

        next(button for button in ui.button if button.label == "下一页 →").click().run()
        assert not ui.exception
        assert next(number for number in ui.number_input if number.label == "页码").value == 2

        before_clear = len(calls)
        ui.button(key="news_clear").click().run()
        assert not ui.exception
        new_calls = calls[before_clear:]
        assert new_calls and all(method == "GET" for method, _ in new_calls)

        assert next(number for number in ui.number_input if number.label == "页码").value == 1
        assert any("27 篇文章" in caption.value for caption in ui.caption)
        assert ui.text_input(key="news_q").value == ""
        assert next(radio for radio in ui.radio if radio.label == "阅读清单").value == "all"
        summary = next(caption.value for caption in ui.caption if caption.value.startswith("当前筛选："))
        for part in [
            "关键词：无",
            "清单：全部",
            "行业：全部行业",
            "来源：全部来源",
            "语言：全部语言",
            "时间：全部时间",
            "分析：全部",
        ]:
            assert part in summary
        assert dict(ui.session_state["_reading_preferences"]) == {"size": 22, "width": "宽松"}
        assert ui.selectbox(key="news_sort").value == "published_at"
        assert ui.toggle(key="news_compact").value is True
        assert news_client.get("/api/v1/articles?reading_state=saved").json()["total"] == 1
