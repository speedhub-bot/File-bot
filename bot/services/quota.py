from __future__ import annotations

import shutil
from pathlib import Path

from bot.config import settings
from bot.db.db import User, _SessionMaker
from bot.db.repo import reset_quota_if_needed
from bot.utils.format import bytes_human


class QuotaError(Exception):
    """Raised when a job would exceed disk or per-user budget."""


def free_disk_bytes(path: Path | None = None) -> int:
    p = path or settings.work_dir
    return shutil.disk_usage(p).free


def disk_used_bytes() -> int:
    """Bytes currently sitting in the working directory tree."""
    total = 0
    for f in settings.work_dir.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def _is_privileged(user) -> bool:
    """Admin or VIP — exempt from per-user daily quota and queue gating.
    Disk headroom still applies to everyone (it's a safety net, not a policy)."""
    if user is None:
        return False
    if user.user_id == settings.admin_id:
        return True
    return bool(getattr(user, "is_vip", False))


async def assert_can_accept(user_id: int, file_size: int) -> None:
    """Raise QuotaError if accepting a file of this size is unsafe."""

    # 0. Ban gate: defense in depth in case the handler check was bypassed.
    async with _SessionMaker() as s:
        u = await s.get(User, user_id)
        if u is not None and u.is_banned:
            raise QuotaError("You are banned from using this bot.")
    privileged = _is_privileged(u)

    # 1. Headroom on disk: need ~2.0x the file size — once for the original on
    #    disk plus the part files we'll write before each upload+delete.
    headroom = file_size * 2
    used = disk_used_bytes()
    budget_left = max(0, settings.disk_budget_bytes - used)
    free_left = free_disk_bytes()
    available = min(budget_left, free_left)
    if headroom > available:
        raise QuotaError(
            f"Not enough scratch space right now (need ~{bytes_human(headroom)}, have "
            f"{bytes_human(available)}). Try again once another job finishes."
        )

    # 2. Per-user daily quota — skipped for admin / VIP.
    if not privileged and settings.per_user_daily_bytes > 0:
        u = await reset_quota_if_needed(user_id)
        if u.daily_used_bytes + file_size > settings.per_user_daily_bytes:
            remaining = max(0, settings.per_user_daily_bytes - u.daily_used_bytes)
            raise QuotaError(
                f"Daily quota of {bytes_human(settings.per_user_daily_bytes)} would be "
                f"exceeded. Remaining today: {bytes_human(remaining)}."
            )
