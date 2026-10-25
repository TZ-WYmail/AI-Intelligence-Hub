from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from app.db import get_session
from app.main import app
from app.models import Job, Metric, Source, now


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


def test_run_logs_filter_and_retry_actions(db):
    db.add(Source(id="off", name="Off", url="https://example.com/off", enabled=False))
    db.flush()
    db.add_all(
        [
            Job(
                id="failed-collect",
                job_type="collect",
                source_id="source",
                payload={},
                status="failed",
                attempt_count=2,
                last_error="HTTP_500",
                scheduled_at=now(),
            ),
            Job(
                id="failed-disabled-source",
                job_type="collect",
                source_id="off",
                payload={},
                status="failed",
                attempt_count=2,
                last_error="HTTP_500",
                scheduled_at=now(),
            ),
            Job(
                id="failed-notify",
                job_type="notify",
                payload={"notification_id": "note"},
                status="failed",
                attempt_count=2,
                last_error="HTTP_500",
                scheduled_at=now(),
            ),
            Job(
                id="completed-collect",
                job_type="collect",
                source_id="source",
                payload={},
                status="completed",
                attempt_count=1,
                scheduled_at=now(),
            ),
        ]
    )
    db.commit()
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
                ui.sidebar.radio[0].set_value("运行日志").run()
                assert not ui.exception

                # Raw task details stay available, while the visible table is Chinese-labeled.
                assert len(ui.dataframe) >= 2
                assert "已完成" in ui.dataframe[0].value["状态"].tolist()
                assert "采集" in ui.dataframe[0].value["任务类别"].tolist()

                ui.selectbox[0].set_value("failed").run()
                assert not ui.exception
                visible = ui.dataframe[0].value
                assert "failed-collect" in visible["任务 ID"].tolist()
                assert "completed-collect" not in visible["任务 ID"].tolist()
                assert "失败" in visible["状态"].tolist()

                # Options show the full job ID; selection uses the real ID value.
                options = ui.multiselect[0].options
                assert any("failed-collect" in option for option in options)
                assert any("failed-disabled-source" in option for option in options)
                assert not any("failed-notify" in option for option in options)
                ui.multiselect[0].set_value(["failed-collect", "failed-disabled-source"]).run()
                assert ui.multiselect[0].value == ["failed-collect", "failed-disabled-source"]
                next(button for button in ui.button if button.label == "重试所选失败任务").click().run()
                assert not ui.exception

                # One task is queued, the rejected one is reported without hiding it.
                assert any("已入队 1 个重试任务" in message.value for message in ui.success)
                assert any("failed-disabled-source" in message.value for message in ui.error)

                # Notification records and runtime metrics are still rendered.
                assert any(item.value == "通知投递" for item in ui.subheader)
                assert any(item.value == "运行指标" for item in ui.subheader)
        db.expire_all()
        assert db.get(Job, "failed-collect").status == "pending"
        assert db.get(Job, "failed-disabled-source").status == "failed"
        assert db.get(Job, "failed-disabled-source").attempt_count == 2
        assert db.get(Job, "failed-disabled-source").last_error == "HTTP_500"
    finally:
        app.dependency_overrides.clear()


def test_dashboard_non_json_gateway_error_stops_safely():
    class ErrorResponse:
        status_code = 503
        text = "<html><body>upstream secret</body></html>"

        def json(self):
            raise ValueError("not JSON")

    with patch("httpx.request", lambda *args, **kwargs: ErrorResponse()):
        ui = AppTest.from_file(str(Path("dashboard/Home.py").resolve()), default_timeout=20).run()
        ui.text_input[0].input("test-admin").run()
        assert not ui.exception
        # The page heading is rendered before the first API call and must survive.
        assert any(title.value == "概览" for title in ui.title)
        messages = [error.value for error in ui.error]
        assert messages == ["API 服务暂不可用（HTTP 503），请稍后重试。"]
        assert all("secret" not in message for message in messages)
        # Business data (metrics, tables, sections) must not render after the error.
        assert not ui.metric
        assert not ui.dataframe
        assert not ui.subheader


def test_overview_labels_heartbeats_by_age_without_calling_them_healthy(db):
    db.add(
        Metric(
            metric_name="scheduler_heartbeat",
            metric_value=1,
            recorded_at=now() - timedelta(days=7),
        )
    )
    future_beat = Metric(
        metric_name="worker_heartbeat",
        metric_value=1,
        recorded_at=now() + timedelta(hours=2),
    )
    db.add(future_beat)
    db.commit()
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

                lines = "\n".join(item.value for item in ui.markdown)
                assert "调度心跳：" in lines and "未观测到近期心跳" in lines
                assert "Worker 心跳：" in lines and "记录时间在未来，无法判断" in lines
                assert "近期有心跳" not in lines
                assert any("最近 24 小时没有新文章" in item.value for item in ui.info)
                assert any("库中最新文章，不限 24 小时" in item.value for item in ui.caption)

                # Missing worker heartbeat is reported as no record, never as healthy.
                db.delete(future_beat)
                db.commit()
                ui.run()
                assert not ui.exception
                lines = "\n".join(item.value for item in ui.markdown)
                assert "Worker 心跳：" in lines and "暂无记录" in lines
                assert "近期有心跳" not in lines

                # A genuinely recent heartbeat is the only case labelled recent.
                db.add(
                    Metric(
                        metric_name="worker_heartbeat",
                        metric_value=1,
                        recorded_at=now(),
                    )
                )
                db.commit()
                ui.run()
                assert not ui.exception
                lines = "\n".join(item.value for item in ui.markdown)
                assert "Worker 心跳：" in lines and "近期有心跳" in lines
    finally:
        app.dependency_overrides.clear()
