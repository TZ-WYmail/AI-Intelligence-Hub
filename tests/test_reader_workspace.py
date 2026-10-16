from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from streamlit.testing.v1 import AppTest


def test_reader_navigation_saved_unread_and_preferences(news_client):
    def request(method, url, **kwargs):
        return news_client.request(
            method,
            urlsplit(url).path,
            headers=kwargs.get("headers"),
            json=kwargs.get("json"),
            params=kwargs.get("params"),
        )

    with patch("httpx.request", request):
        ui = AppTest.from_file(str(Path("dashboard/Home.py").resolve()), default_timeout=20).run()
        ui.text_input[0].input("test-admin").run()
        ui.sidebar.radio[0].set_value("实时新闻").run()
        next(button for button in ui.button if button.label == "开始阅读").click().run()
        assert not ui.exception
        assert any(button.label == "← 返回列表" for button in ui.button)
        assert news_client.get("/api/v1/articles?reading_state=read").json()["total"] == 1
        next(button for button in ui.button if button.label == "☆ 收藏这篇").click().run()
        assert not ui.exception
        assert news_client.get("/api/v1/articles?reading_state=saved").json()["total"] == 1
        ui.select_slider[0].set_value(22).run()
        next(button for button in ui.button if button.label == "下一篇").click().run()
        assert not ui.exception and ui.select_slider[0].value == 22
        assert news_client.get("/api/v1/articles?reading_state=read").json()["total"] == 2
        next(button for button in ui.button if button.label == "标为未读").click().run()
        assert news_client.get("/api/v1/articles?reading_state=unread").json()["total"] == 1
        next(button for button in ui.button if button.label == "← 返回列表").click().run()
        assert not ui.exception
        next(radio for radio in ui.radio if radio.label == "阅读清单").set_value("saved").run()
        assert not ui.exception
        assert len([button for button in ui.button if button.label == "开始阅读"]) == 1


def test_source_html_is_escaped_in_reader():
    from dashboard.news import reading_minutes, snippet

    assert snippet("a " * 200, 50).endswith("…")
    assert reading_minutes("中文" * 500) == 3


def test_reader_returns_to_page_and_filter_without_losing_place(news_client, db):
    from sqlalchemy import select

    from app.models import Article, now

    source_article = db.scalar(select(Article))
    for index in range(25):
        db.add(
            Article(
                source_id=source_article.source_id,
                title=f"Extra article {index}",
                summary="Readable summary",
                original_url=f"https://example.com/extra/{index}",
                canonical_url=f"https://example.com/extra/{index}",
                content_hash=f"extra-{index}",
                published_at=now(),
            )
        )
    db.commit()

    def request(method, url, **kwargs):
        return news_client.request(
            method,
            urlsplit(url).path,
            headers=kwargs.get("headers"),
            json=kwargs.get("json"),
            params=kwargs.get("params"),
        )

    with patch("httpx.request", request):
        ui = AppTest.from_file(str(Path("dashboard/Home.py").resolve()), default_timeout=20).run()
        ui.text_input[0].input("test-admin").run()
        ui.sidebar.radio[0].set_value("实时新闻").run()
        next(button for button in ui.button if button.label == "下一页 →").click().run()
        assert not ui.exception
        assert next(number for number in ui.number_input if number.label == "页码").value == 2
        next(button for button in ui.button if button.label == "开始阅读").click().run()
        next(button for button in ui.button if button.label == "← 返回列表").click().run()
        assert not ui.exception
        assert next(number for number in ui.number_input if number.label == "页码").value == 2
        next(radio for radio in ui.radio if radio.label == "阅读清单").set_value("unread").run()
        assert next(number for number in ui.number_input if number.label == "页码").value == 1


def test_reader_prose_escapes_untrusted_markup():
    from dashboard.news import prose

    with patch("dashboard.news.st.markdown") as render:
        prose('<script>alert(1)</script><img src="https://attacker.example/x">', 18)
        markup = render.call_args.args[0]
        assert "<script>" not in markup and "<img" not in markup
        assert "&lt;script&gt;" in markup
