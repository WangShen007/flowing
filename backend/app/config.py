import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "飞序 Flowing"
    APP_ENV: str = "development"
    DEBUG: bool = True
    API_PREFIX: str = "/api/v1"
    CORS_ALLOWED_ORIGINS: str = ""  # Same-origin deployment requires no CORS permissions.

    @field_validator("CORS_ALLOWED_ORIGINS")
    @classmethod
    def validate_cors_origins(cls, value: str) -> str:
        from urllib.parse import urlsplit

        for origin in filter(None, (item.strip() for item in value.split(","))):
            parsed = urlsplit(origin)
            if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path
                    or parsed.query or parsed.fragment or parsed.username or parsed.password or "*" in origin):
                raise ValueError("CORS origins must be explicit http(s) origins without paths or wildcards")
        return value

    LLM_PROVIDER: str = "openai"
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None
    LLM_MODEL: str = "qwen-plus"
    ANTHROPIC_API_KEY: Optional[str] = None

    LARK_CLI_LLM_TIMEOUT: int = 25
    LARK_AGENT_ENGINE: str = "langgraph"
    CHAT_MAX_ACTIVE_PER_PROCESS: int = Field(default=16, ge=1, le=256)
    CHAT_MAX_ACTIVE_PER_ACCOUNT: int = Field(default=2, ge=1, le=32)
    # Plan previews have a smaller model budget than full task execution. The
    # API timeout is kept outside that budget so the planner can return its
    # deterministic fallback instead of being cancelled mid-cleanup.
    LARK_CLI_PLAN_LLM_TIMEOUT: int = 8
    LARK_CLI_PLAN_TIMEOUT: int = 12
    LARK_CLI_COMMAND_TIMEOUT: int = 30
    FRONTEND_DIST_DIR: str = ""
    SCHEDULED_TASKS_ENABLED: bool = True
    SCHEDULED_TASK_POLL_SECONDS: int = 30
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_TENANT_KEY: str = ""
    FEISHU_REDIRECT_URI: str = ""
    FEISHU_TOKEN_ENCRYPTION_KEY: str = ""
    AUTH_SESSION_DAYS: int = 30
    AUTH_BOOTSTRAP_ACCOUNT: str = "admin"
    AUTH_BOOTSTRAP_PASSWORD: str = ""  # Empty: no automatic password account creation.
    FEISHU_MANAGED_TASKLIST_IDS: str = ""  # Explicit comma-separated team tasklist GUIDs; no global admin bypass.
    BOT_CHANNELS_ENABLED: bool = True
    TELEGRAM_BOT_TOKEN: str = ""  # Server secret; one dedicated bot, private chats only.

    model_config = SettingsConfigDict(
        env_file=(
            ".env",
            "../.env",
            str(
                Path(os.environ.get("FEISHU_CLI_DATA_DIR") or Path(__file__).resolve().parents[2] / ".feishu_cli_data")
                / "enterprise.env"
            ),
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
