from __future__ import annotations

import logging
import time
from typing import Any

from aiohttp import web

from bot.config import settings
from bot.services.quota import disk_used_bytes, free_disk_bytes

log = logging.getLogger(__name__)

_started_at = time.monotonic()


async def _health(_: web.Request) -> web.Response:
    """Cheap liveness probe used by Railway / Fly / Koyeb to keep the
    container alive. Returns 200 once the process has booted."""
    return web.json_response(
        {
            "status": "ok",
            "uptime_sec": int(time.monotonic() - _started_at),
        }
    )


async def _ready(_: web.Request) -> web.Response:
    """Readiness probe: returns 200 only once we have at least some disk
    headroom, otherwise 503 so the host can refuse traffic."""
    used = disk_used_bytes()
    free = free_disk_bytes()
    budget_left = max(0, settings.disk_budget_bytes - used)
    body: dict[str, Any] = {
        "status": "ok",
        "disk_used": used,
        "disk_free": free,
        "budget_left": budget_left,
    }
    if budget_left < 32 * 1024 * 1024 or free < 32 * 1024 * 1024:
        body["status"] = "degraded"
        return web.json_response(body, status=503)
    return web.json_response(body)


async def _root(_: web.Request) -> web.Response:
    return web.Response(text="File Bot is running. POST /health for liveness, /ready for readiness.\n")


async def start_health_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", _root)
    app.router.add_get("/health", _health)
    app.router.add_get("/ready", _ready)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, settings.health_host, settings.port)
    try:
        await site.start()
        log.info("Health server listening on %s:%s", settings.health_host, settings.port)
    except OSError as e:
        # Port already in use — log and continue. Don't crash the bot for a
        # health probe that isn't strictly required (e.g. Hetzner).
        log.warning("Health server bind failed (%s); continuing without it", e)
    return runner
