import hashlib
from typing import Protocol

import httpx

from app.core.config import Settings
from app.core.security import RemoteError


def recipient_key(channel: str, config: Settings) -> str:
    destination = config.telegram_chat_id if channel == "telegram" else config.feishu_webhook_url
    return hashlib.sha256((channel + ":" + destination).encode()).hexdigest()


def message(payload: dict) -> str:
    return (
        f"[{payload['level']}] {payload['title']}\n"
        f"行业：{', '.join(payload.get('industries', []))}\n"
        f"评分：{payload['score']} · 验证：{payload.get('verification', 'unverified')}\n"
        f"事件时间：{payload.get('published_at', '')}\n发现时间：{payload.get('collected_at', '')}\n"
        f"{payload['summary'][:1200]}\n来源：{payload.get('source', '')}\n{payload['url']}\n"
        f"事件：/api/v1/events/{payload.get('event_id', '')}"
    )[:3800]


class Notifier(Protocol):
    def send(self, payload: dict) -> None: ...


class WebhookNotifier:
    def __init__(self, channel: str, config: Settings):
        self.channel, self.config = channel, config

    def send(self, payload: dict) -> None:
        cfg = self.config
        if self.channel == "telegram":
            url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
            body = {
                "chat_id": cfg.telegram_chat_id,
                "text": message(payload),
                "disable_web_page_preview": True,
            }
        elif self.channel == "feishu":
            url = cfg.feishu_webhook_url
            body = {"msg_type": "text", "content": {"text": message(payload)}}
        else:
            raise ValueError("UNKNOWN_CHANNEL")
        with httpx.Client(timeout=cfg.http_timeout, trust_env=False) as client:
            response = client.post(url, json=body)
        if response.status_code >= 400:
            retry = response.headers.get("retry-after", "60")
            raise RemoteError(
                f"NOTIFY_HTTP_{response.status_code}", min(int(retry), 86400) if retry.isdigit() else 60
            )
        data = response.json()
        if (self.channel == "telegram" and data.get("ok") is not True) or (
            self.channel == "feishu" and data.get("code") != 0
        ):
            raise RemoteError("NOTIFY_REJECTED")
