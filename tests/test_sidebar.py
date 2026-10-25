from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
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
    assert not any("本次连接已验证" in item.value for item in ui.sidebar.markdown)

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


def test_sidebar_wrong_key_does_not_show_verified(db):
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
                ui.text_input[0].input("wrong-key").run()
                assert not ui.exception
                assert ui.text_input[0].value == "wrong-key"
                assert any("401" in item.value for item in ui.error)
                assert not any("本次连接已验证" in item.value for item in ui.sidebar.markdown)
    finally:
        app.dependency_overrides.clear()


def test_sidebar_keeps_key_and_logout_clears_only_local_session(db):
    app.dependency_overrides[get_session] = lambda: db
    try:
        with TestClient(app) as client:
            calls = []

            def request(method, url, **kwargs):
                parsed = urlsplit(url)
                calls.append(parsed.path)
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
                assert ui.text_input[0].value == "test-admin"
                assert any("本次连接已验证" in item.value for item in ui.sidebar.markdown)

                ui.sidebar.radio[0].set_value("实时新闻").run()
                assert not ui.exception
                assert ui.sidebar.radio[0].value == "实时新闻"
                key_input = next(item for item in ui.text_input if item.label == "管理员密钥")
                assert key_input.value == "test-admin"
                assert any("本次连接已验证" in item.value for item in ui.sidebar.markdown)

                ui.session_state["_reader"] = {"id": "reader", "ids": ["reader"], "marked": False}
                ui.session_state["_analysis_requested"] = "reader"
                ui.session_state["_reading_preferences"] = {"size": 20, "width": "宽"}
                ui.session_state["_custom_brief"] = {"markdown": "secret"}
                calls.clear()
                ui.button(key="logout_session").click().run()
                assert not ui.exception
                assert ui.text_input[0].value == ""
                assert ui.session_state.get("admin_key") == ""
                assert ui.sidebar.radio[0].value == "实时新闻"
                assert not any("本次连接已验证" in item.value for item in ui.sidebar.markdown)
                assert ui.session_state.get("_reader") is None
                assert ui.session_state.get("_analysis_requested") is None
                assert ui.session_state.get("_reading_preferences") is None
                assert ui.session_state.get("_custom_brief") is None
                assert calls == []
                assert any("让分散的信息" in item.value for item in ui.title)

                ui.text_input[0].input("test-admin").run()
                assert not ui.exception
                key_input = next(item for item in ui.text_input if item.label == "管理员密钥")
                assert key_input.value == "test-admin"
                assert ui.session_state.get("admin_key") == "test-admin"
                assert ui.sidebar.radio[0].value == "实时新闻"
                assert any("本次连接已验证" in item.value for item in ui.sidebar.markdown)
    finally:
        app.dependency_overrides.clear()


def test_sidebar_key_change_and_network_failure_clear_verified(db):
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
                assert any("本次连接已验证" in item.value for item in ui.sidebar.markdown)

                key_input = next(item for item in ui.text_input if item.label == "管理员密钥")
                key_input.input("wrong-key").run()
                assert not ui.exception
                assert any("401" in item.value for item in ui.error)
                assert not any("本次连接已验证" in item.value for item in ui.sidebar.markdown)
                assert ui.session_state.get("admin_key") == "wrong-key"

                key_input = next(item for item in ui.text_input if item.label == "管理员密钥")
                key_input.input("test-admin").run()
                assert not ui.exception
                assert any("本次连接已验证" in item.value for item in ui.sidebar.markdown)

                def down(*args, **kwargs):
                    raise httpx.ConnectError("down")

                with patch("httpx.request", down):
                    ui.sidebar.radio[0].set_value("实时新闻").run()
                assert not ui.exception
                assert any("无法连接 API" in item.value for item in ui.error)
                assert not any("本次连接已验证" in item.value for item in ui.sidebar.markdown)
                assert ui.session_state.get("admin_key") == "test-admin"
    finally:
        app.dependency_overrides.clear()
