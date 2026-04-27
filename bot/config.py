from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(..., alias="BOT_TOKEN")
    api_id: int = Field(..., alias="API_ID")
    api_hash: str = Field(..., alias="API_HASH")
    admin_id: int = Field(..., alias="ADMIN_ID")

    work_dir: Path = Field(default=Path("./work"), alias="WORK_DIR")
    disk_budget_bytes: int = Field(default=943_718_400, alias="DISK_BUDGET_BYTES")
    per_user_daily_bytes: int = Field(default=10_737_418_240, alias="PER_USER_DAILY_BYTES")

    # HTTP health endpoint — Railway / Fly / Koyeb expect *something* bound
    # to $PORT, otherwise they consider the deploy unhealthy and kill it.
    port: int = Field(default=8080, alias="PORT")
    health_host: str = Field(default="0.0.0.0", alias="HEALTH_HOST")

    default_auto_part_bytes: int = Field(default=52_428_800, alias="DEFAULT_AUTO_PART_BYTES")
    max_part_bytes: int = Field(default=2_093_796_556, alias="MAX_PART_BYTES")
    max_concurrent_jobs: int = Field(default=2, alias="MAX_CONCURRENT_JOBS")

    database_url: str = Field(
        default="sqlite+aiosqlite:///./filebot.db", alias="DATABASE_URL"
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


settings = Settings()  # type: ignore[call-arg]
settings.work_dir.mkdir(parents=True, exist_ok=True)
