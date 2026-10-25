from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

PAGES = ["概览", "实时新闻", "重大事件", "行业管理", "数据源管理", "简报", "系统设置", "运行日志"]


def test_login_explains_entry_without_prefilled_key_or_api_calls():
    def no_api(*args, **kwargs):
        raise AssertionError("API must not be called before a key is entered")

    with patch("httpx.request", no_api):
        ui = AppTest.from_file(str(Path("dashboard/Home.py").resolve()), default_timeout=20).run()

    assert not ui.exception
    assert len(ui.text_input) == 1
    key_input = ui.text_input[0]
    assert key_input.label == "管理员密钥"
    assert key_input.value == ""
    assert key_input.proto.type == 1  # password input
    assert key_input.proto.autocomplete == "off"
    assert ui.sidebar.radio[0].options == PAGES
    assert not any(title.value == "概览" for title in ui.title)

    text = "\n".join(item.value for item in ui.markdown)
    assert "如何进入" in text
    assert "ADMIN_API_KEY" in text
    assert "密码类型" in text
    assert "进入左侧已选择的工作空间（默认「概览」）" in text
    assert "aih-login-lede" in text
    assert "test-admin" not in text
