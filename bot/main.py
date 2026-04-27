from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from pyrogram import Client

from bot.config import settings
from bot.db.db import init_db
from bot.handlers import admin, errors, files, splits, start

log = logging.getLogger(__name__)


def _build_client() -> Client:
    return Client(
        name="filebot",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        bot_token=settings.bot_token,
        in_memory=True,  # don't write a session file to disk
        max_concurrent_transmissions=2,
    )


def _setup_logging() -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Quiet pyrogram's own internal chatter unless we asked for DEBUG.
    if settings.log_level.upper() != "DEBUG":
        for noisy in ("pyrogram", "pyrogram.session", "pyrogram.connection"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


async def _amain() -> None:
    _setup_logging()
    await init_db()

    app = _build_client()
    start.register(app)
    files.register(app)
    splits.register(app)
    admin.register(app)
    errors.register(app)

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


def main() -> None:
    try:
        import uvloop  # type: ignore

        uvloop.install()
    except ImportError:
        pass
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
