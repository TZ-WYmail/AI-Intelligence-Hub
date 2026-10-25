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


def test_news_card_escapes_untrusted_text_and_keeps_summary_labels():
    from dashboard.news import news_card_html

    item = {
        "id": "card",
        "title": '<img src="https://attacker.example/x" onerror="alert(1)">',
        "summary": "<script>alert(1)</script> source summary",
        "ai_summary_zh": None,
        "source_id": "source",
        "published_at": "2026-10-25T10:00:00+00:00",
        "importance_score": 85,
        "industry_ids": ["ai"],
        "bookmarked": True,
        "read_at": None,
    }
    names = {"source": "<i>Source</i>"}
    industries = {"ai": "<b>Industry</b>"}
    markup = news_card_html(item, names, industries, lambda _: "10-25 10:00")
    assert "<script>" not in markup and "<img" not in markup
    assert "<i>" not in markup and "<b>Industry</b>" not in markup
    assert "&lt;script&gt;" in markup and "&lt;img" in markup
    assert "来源摘要" in markup and "未核验" in markup and "重要性 85" in markup
    assert "★ 收藏" in markup and "未读" in markup

    ai_markup = news_card_html(
        {**item, "ai_summary_zh": "<b>AI summary</b>"},
        names,
        industries,
        lambda _: "10-25 10:00",
    )
    assert "AI 中文摘要" in ai_markup
    assert "&lt;b&gt;AI summary&lt;/b&gt;" in ai_markup

    compact = news_card_html(item, names, industries, lambda _: "10-25 10:00", compact=True)
    assert "来源摘要" not in compact
    assert "重要性 85" in compact
    assert "Source" in compact and "&lt;img" in compact


def test_reader_dedupes_identical_source_summary_and_bottom_nav_keeps_preferences(news_client, db):
    from datetime import timedelta

    from sqlalchemy import select

    from app.models import Article, now

    ai_article = db.scalar(select(Article).order_by(Article.title))
    other = db.scalar(select(Article).where(Article.id != ai_article.id))
    ai_article.summary = "重复来源摘要内容"
    ai_article.content = "\n  重复来源摘要内容  \n"
    ai_article.collected_at = now()
    other.collected_at = now() - timedelta(minutes=5)
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
        ui = AppTest.from_file(str(Path("dashboard/Home.py").resolve()), default_timeout=30).run()
        ui.text_input[0].input("test-admin").run()
        ui.sidebar.radio[0].set_value("实时新闻").run()
        ui.button(key="detail_" + ai_article.id).click().run()
        assert not ui.exception

        speed = "\n".join(item.value for item in ui.tabs[0].markdown)
        assert "事件概述" in speed and "AI 生成信息" in speed
        assert any("当前 AI 调用未启用" in item.value for item in ui.tabs[0].caption)
        assert any("速读约" in item.value for item in ui.markdown)
        assert any("不代表全文阅读时间" in item.value for item in ui.caption)

        ui.select_slider[0].set_value(22).run()
        next(radio for radio in ui.radio if radio.label == "阅读宽度").set_value("宽松").run()
        assert not ui.exception

        original = "\n".join(item.value for item in ui.tabs[1].markdown)
        assert original.count("重复来源摘要内容") == 1
        assert "来源摘要" in original and "来源正文" not in original

        ui.button(key="reader-bottom-next").click().run()
        assert not ui.exception
        assert any("第 2 / 2 篇" in item.value for item in ui.caption)
        assert ui.select_slider[0].value == 22
        assert next(radio for radio in ui.radio if radio.label == "阅读宽度").value == "宽松"

        text = "\n".join(item.value for item in ui.markdown)
        text += "\n" + "\n".join(item.value for item in ui.caption)
        text += "\n" + "\n".join(item.value for item in ui.info)
        assert "规则/来源模式" in text
        assert not any(button.label == "生成中文解读（计入 AI 配额）" for button in ui.button)
