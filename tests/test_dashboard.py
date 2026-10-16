from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from app.db import get_session
from app.main import app


def test_dashboard_pages(db):
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
                assert not ui.exception
                ui.text_input[0].input("test-admin").run()
                assert not ui.exception
                for label in [
                    "实时新闻",
                    "重大事件",
                    "行业管理",
                    "数据源管理",
                    "简报",
                    "系统设置",
                    "运行日志",
                ]:
                    ui.sidebar.radio[0].set_value(label).run()
                    assert not ui.exception, f"Page failed: {label}"
    finally:
        app.dependency_overrides.clear()
