from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from app.db import get_session
from app.main import app

PAGES = ["概览", "实时新闻", "重大事件", "行业管理", "数据源管理", "简报", "系统设置", "运行日志"]


def test_sidebar_keeps_one_native_radio_all_pages_and_login_has_no_api(db):
    def no_api(*args, **kwargs):
        raise AssertionError("API must not be called before a key is entered")

    with patch("httpx.request", no_api):
        ui = AppTest.from_file(str(Path("dashboard/Home.py").resolve()), default_timeout=20).run()
    assert not ui.exception
    assert len(ui.sidebar.radio) == 1
    nav = ui.sidebar.radio[0]
    assert nav.label == "工作空间"
    assert nav.options == PAGES

    app.dependency_overrides[get_session] = lambda: db
    try:
        with TestClient(app) as client:

            def request(method, url, **kwargs):
                parsed = urlsplit(url)
                return client.request(
                    method,
                    parsed.path,
                    headers=kwargs.get("headers"),
                    json=kwargs.get("json"),
                    params=kwargs.get("params"),
                )

            with patch("httpx.request", request):
                ui = AppTest.from_file(str(Path("dashboard/Home.py").resolve()), default_timeout=20).run()
                ui.text_input[0].input("test-admin").run()
                assert not ui.exception
                assert len(ui.sidebar.radio) == 1
                assert ui.sidebar.radio[0].options == PAGES
                for page in PAGES:
                    ui.sidebar.radio[0].set_value(page).run()
                    assert not ui.exception, f"Page failed: {page}"
                    assert ui.sidebar.radio[0].value == page
    finally:
        app.dependency_overrides.clear()
