"""Centralised settings loaded from environment / .env file."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "/app/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Execution
    mode: str = "daemon"
    config_path: str = "/data/config.json"
    poll_minutes: int = 10

    # Email / SMTP
    email_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_use_ssl: bool = False
    email_to: str = ""
    email_theme: str = "dark"

    # Storage
    db_path: str = "/data/wishlist_state.sqlite3"

    # Logging
    log_level: str = "INFO"
    log_to_file: bool = True
    log_file: str = "/data/wishlist_monitor.log"
    log_to_stdout: bool = True

    # Pruning / retention
    debug_dump_prune_enabled: bool = True
    debug_dir: str = "/data/debug_dumps"
    debug_dump_max_age_days: int = 7
    log_prune_enabled: bool = True
    log_max_age_days: int = 7


settings = Settings()
