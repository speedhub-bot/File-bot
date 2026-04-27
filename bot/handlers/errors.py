from __future__ import annotations

import logging

from pyrogram import Client
from pyrogram.types import Update

log = logging.getLogger(__name__)


def register(app: Client) -> None:
    """Pyrogram doesn't expose a single global error hook, but we wrap each
    handler with try/except inside the services layer. This module exists so
    the import pattern in main.py is uniform and so we can attach a raw
    update logger if needed."""

    @app.on_raw_update()
    async def _trace(_: Client, update: Update, *args, **kwargs) -> None:  # noqa: ARG001
        if log.isEnabledFor(logging.DEBUG):
            log.debug("raw update: %s", type(update).__name__)
