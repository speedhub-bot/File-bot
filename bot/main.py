from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from contextlib import suppress

from pyrogram import Client

log = logging.getLogger(__name__)


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    if level.upper() != "DEBUG":
        for noisy in ("pyrogram", "pyrogram.session", "pyrogram.connection"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def _load_settings_or_die():
    """Import settings late so we can show a friendly error if env vars are
    missing — this is by far the most common cause of "deployment crashed"
    on Railway/Fly when the user hasn't set the variables yet."""
    try:
        from bot.config import settings  # noqa: WPS433  (intentional late import)
        return settings
    except Exception as e:  # noqa: BLE001
        # Bootstrap a minimal logger so the message lands in the platform logs.
        _setup_logging("INFO")
        missing = []
        text = str(e).lower()
        for var in ("bot_token", "api_id", "api_hash", "admin_id"):
            if var in text:
                missing.append(var.upper())
        log.error("=" * 64)
        log.error("FATAL: configuration could not be loaded.")
        log.error("       %s", e)
        if missing:
            log.error(
                "       Likely missing env vars: %s",
                ", ".join(missing),
            )
        log.error(
            "       In Railway/Fly/Koyeb dashboards add: "
            "BOT_TOKEN, API_ID, API_HASH, ADMIN_ID."
        )
        log.error("       See README.md → Credentials.")
        log.error("       Detected env keys present: %s",
                  sorted(k for k in os.environ if k in {
                      "BOT_TOKEN", "API_ID", "API_HASH", "ADMIN_ID",
                      "WORK_DIR", "DATABASE_URL", "PORT",
                  }))
        log.error("=" * 64)
        sys.exit(1)


def _build_client(settings) -> Client:
    from pyrogram import enums

    client = Client(
        name="filebot",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        bot_token=settings.bot_token,
        in_memory=True,
        max_concurrent_transmissions=2,
    )
    # Pin the parse mode to MARKDOWN-only. The default in Pyrogram 2 is
    # MARKDOWN+HTML *combined*, which means literal `<id>`/`<text>`
    # placeholders inside help strings get treated as unknown HTML tags
    # and silently stripped. MARKDOWN-only keeps the angle brackets intact.
    client.parse_mode = enums.ParseMode.MARKDOWN
    return client


async def _amain() -> None:
    settings = _load_settings_or_die()
    _setup_logging(settings.log_level)

    # Imports kept inline so the friendly settings error above can fire even
    # if a downstream module has its own import-time problem.
    from bot.db.db import init_db
    from bot.handlers import admin, errors, files, merge, splits, start, url
    from bot.services.health import start_health_server

    await init_db()

    health_runner = await start_health_server()

    app = _build_client(settings)
    for mod in (start, files, splits, admin, errors, url, merge):
        mod.register(app)

    log.info("Starting File Bot…")
    await app.start()
    me = await app.get_me()
    log.info("Logged in as @%s (id=%s)", me.username, me.id)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    log.info("Shutting down…")
    await app.stop()
    await health_runner.cleanup()


def main() -> None:
    try:
        import uvloop  # type: ignore

        uvloop.install()
    except ImportError:
        pass
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
