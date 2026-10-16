from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    app_timezone: str = "Asia/Shanghai"
    database_url: str = "sqlite:///./data/aih.db"
    admin_api_key: str = ""
    api_base_url: str = "http://127.0.0.1:8000"
    ai_enabled: bool = False
    ai_api_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = ""
    ai_model: str = ""
    ai_thinking_mode: Literal["provider_default", "disabled", "enabled"] = "provider_default"
    ai_timeout: float = Field(30, ge=1, le=90)
    ai_max_output_tokens: int = Field(1200, ge=100, le=8000)
    ai_daily_request_limit: int = Field(100, ge=0)
    ai_input_cny_per_million: float = Field(10, ge=0)
    ai_output_cny_per_million: float = Field(30, ge=0)
    monthly_ai_budget_cny: float = Field(50, ge=0)
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    feishu_enabled: bool = False
    feishu_webhook_url: str = ""
    critical_alert_threshold: int = Field(85, ge=0, le=100)
    important_alert_threshold: int = Field(60, ge=0, le=100)
    p1_batch_minutes: int = Field(60, ge=1)
    quiet_start_hour: int = Field(23, ge=0, le=23)
    quiet_end_hour: int = Field(7, ge=0, le=23)
    backup_enabled: bool = True
    backup_interval_hours: int = Field(24, ge=1, le=168)
    backup_keep_count: int = Field(7, ge=1, le=90)
    backup_directory: str = "./data/backups"
    daily_digest_enabled: bool = True
    daily_digest_hour: int = Field(8, ge=0, le=23)
    raw_retention_days: int = Field(30, ge=1)
    notification_retention_days: int = Field(180, ge=1)
    job_retention_days: int = Field(30, ge=1)
    http_timeout: float = Field(20, ge=1, le=60)
    max_response_bytes: int = Field(5_000_000, ge=1024)
    job_max_attempts: int = Field(5, ge=1, le=10)
    job_lease_seconds: int = Field(300, ge=120)

    @model_validator(mode="after")
    def check(self):
        ZoneInfo(self.app_timezone)
        if self.important_alert_threshold > self.critical_alert_threshold:
            raise ValueError("告警阈值顺序错误")
        if self.ai_enabled and not (self.ai_api_key and self.ai_model):
            raise ValueError("AI_ENABLED requires AI_API_KEY and AI_MODEL")
        if self.telegram_enabled and not (self.telegram_bot_token and self.telegram_chat_id):
            raise ValueError("Telegram credentials missing")
        if self.feishu_enabled and not self.feishu_webhook_url.startswith("https://"):
            raise ValueError("Feishu HTTPS webhook missing")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
