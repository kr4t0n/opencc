from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    slack_bot_token: str
    slack_app_token: str
    claude_cli_path: str = "claude"
    claude_work_dir: str = "."
    claude_cli_args: str = "-p"
    claude_extra_args: str = ""

    host: str = "0.0.0.0"
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
