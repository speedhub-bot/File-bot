from __future__ import annotations

import logging
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Embedded default credentials
#
# ⚠️  SECURITY NOTICE
#
# These defaults exist so the bot deploys to Railway / Fly / Koyeb with zero
# Variables setup — convenient, but also means every fork of this repo runs
# the same bot identity. If this repo is public (or becomes public), rotate
# the token immediately via @BotFather → /revoke and either:
#   1) replace the constant below with the new token, OR
#   2) override at runtime with the BOT_TOKEN env var (always wins).
#
# The api_id / api_hash identify the *application*, not a user account, so
# leaking them is much less dangerous than the bot token, but still avoid it
# for production deployments.
#
# In ALL cases, env vars override these defaults — set BOT_TOKEN, API_ID,
# API_HASH, ADMIN_ID in your platform dashboard to keep secrets off GitHub.
# ---------------------------------------------------------------------------
DEFAULT_BOT_TOKEN = "8650518989:AAG-JYTYDJ0ezutb_U3OFR18TRef-EX29E8"
DEFAULT_API_ID = 31206680
DEFAULT_API_HASH = "39d0b0430309434e7ab02ab1742dd170"
DEFAULT_ADMIN_ID = 5944410248


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # All four are now *defaulted* but env vars still take precedence.
    bot_token: str = Field(default=DEFAULT_BOT_TOKEN, alias="BOT_TOKEN")
    api_id: int = Field(default=DEFAULT_API_ID, alias="API_ID")
    api_hash: str = Field(default=DEFAULT_API_HASH, alias="API_HASH")
    admin_id: int = Field(default=DEFAULT_ADMIN_ID, alias="ADMIN_ID")

    work_dir: Path = Field(default=Path("./work"), alias="WORK_DIR")
    # Disk cap that the quota check enforces. `0` means "no explicit cap" —
    # the headroom check then falls back to whatever the underlying volume
    # actually has free (`shutil.disk_usage`). Useful when the bot runs on
    # a fat box (TBs of disk) where the old 900 MB Railway-shaped default
    # was the bottleneck.
    disk_budget_bytes: int = Field(default=0, alias="DISK_BUDGET_BYTES")
    per_user_daily_bytes: int = Field(default=0, alias="PER_USER_DAILY_BYTES")

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

    @property
    def using_embedded_defaults(self) -> bool:
        return (
            self.bot_token == DEFAULT_BOT_TOKEN
            and self.api_id == DEFAULT_API_ID
            and self.api_hash == DEFAULT_API_HASH
        )


settings = Settings()  # type: ignore[call-arg]
settings.work_dir.mkdir(parents=True, exist_ok=True)

if settings.using_embedded_defaults:
    log.warning(
        "⚠️  Bot is running on the embedded default credentials. "
        "If this repo is public, rotate the token via @BotFather → /revoke "
        "and either override BOT_TOKEN as an env var or replace the constant "
        "in bot/config.py. Doing nothing means anyone who reads the repo can "
        "drain this bot."
    )
