from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import sys

from pyrogram import Client, filters
from pyrogram.types import Message

from bot.config import settings
from bot.db.repo import (
    all_user_ids,
    get_user,
    list_recent_jobs,
    list_users,
    set_banned,
    set_vip,
    stats,
)
from bot.services.jobs import jobs
from bot.services.quota import disk_used_bytes, free_disk_bytes
from bot.utils.format import bytes_human

log = logging.getLogger(__name__)


def _is_admin(m: Message) -> bool:
    return bool(m.from_user and m.from_user.id == settings.admin_id)


def register(app: Client) -> None:
    @app.on_message(filters.command(["stats"]))
    async def on_stats(_: Client, m: Message) -> None:
        if not _is_admin(m):
            return
        s = await stats()
        used = disk_used_bytes()
        budget_left = max(0, settings.disk_budget_bytes - used)
        await m.reply_text(
            "*📊 Bot stats*\n"
            f"Users: `{s['users']}`\n"
            f"Total jobs: `{s['jobs']}`\n"
            f"Active jobs: `{s['active']}` (cap `{settings.max_concurrent_jobs}`)\n"
            f"Total bytes processed: `{bytes_human(s['bytes'])}`\n"
            f"Disk in use (work_dir): `{bytes_human(used)}`\n"
            f"Disk budget left: `{bytes_human(budget_left)}` of "
            f"`{bytes_human(settings.disk_budget_bytes)}`\n"
            f"Filesystem free: `{bytes_human(free_disk_bytes())}`"
        )

    @app.on_message(filters.command(["jobs"]))
    async def on_jobs(_: Client, m: Message) -> None:
        if not _is_admin(m):
            return
        active = jobs.active_jobs
        recent = await list_recent_jobs(limit=10)
        lines = ["*🛠 Jobs*", "", "*Active:*"]
        if not active:
            lines.append("_(none)_")
        else:
            for j in active:
                lines.append(
                    f"• #{j.job_db_id} `{j.file_name}` "
                    f"({bytes_human(j.file_size)}) mode=`{j.mode}` user=`{j.user_id}`"
                )
        lines += ["", "*Recent:*"]
        for r in recent:
            lines.append(
                f"• #{r.id} `{r.file_name}` ({bytes_human(r.size_bytes)}) "
                f"parts=`{r.parts}` status=`{r.status}`"
            )
        await m.reply_text("\n".join(lines))

    @app.on_message(filters.command(["users"]))
    async def on_users(_: Client, m: Message) -> None:
        if not _is_admin(m):
            return
        rows = await list_users(limit=30)
        if not rows:
            await m.reply_text("No users yet.")
            return
        lines = ["*👥 Top users*"]
        for u in rows:
            tag = f"@{u.username}" if u.username else "(no username)"
            banned = " 🚫" if u.is_banned else ""
            lines.append(
                f"• `{u.user_id}` {tag} jobs=`{u.total_jobs}` "
                f"sent=`{bytes_human(u.total_bytes)}`{banned}"
            )
        await m.reply_text("\n".join(lines))

    @app.on_message(filters.command(["broadcast"]))
    async def on_broadcast(client: Client, m: Message) -> None:
        if not _is_admin(m):
            return
        text = m.text.partition(" ")[2].strip() if m.text else ""
        if not text:
            await m.reply_text("Usage: `/broadcast <message>`")
            return
        # Tag every broadcast with a credit footer so attribution survives
        # forwards. Admin can opt out with `/broadcast --raw <text>`.
        if text.startswith("--raw "):
            text = text[len("--raw "):].strip()
        else:
            text = f"{text}\n\n— _broadcast via_ @akaza\\_inst"
        ids = await all_user_ids()
        sent = 0
        failed = 0
        for uid in ids:
            try:
                await client.send_message(uid, text)
                sent += 1
            except Exception as e:  # noqa: BLE001
                log.warning("broadcast to %s failed: %s", uid, e)
                failed += 1
            await asyncio.sleep(0.05)
        await m.reply_text(f"Broadcast: sent=`{sent}`, failed=`{failed}`.")

    @app.on_message(filters.command(["ban", "unban"]))
    async def on_ban(_: Client, m: Message) -> None:
        if not _is_admin(m):
            return
        parts = (m.text or "").split()
        if len(parts) < 2:
            await m.reply_text(f"Usage: `{parts[0] if parts else '/ban'} <user_id>`")
            return
        try:
            uid = int(parts[1])
        except ValueError:
            await m.reply_text("user_id must be a number.")
            return
        ok = await set_banned(uid, parts[0].lstrip("/").startswith("ban"))
        await m.reply_text("Done." if ok else "User not found.")

    @app.on_message(filters.command(["diag"]))
    async def on_diag(_: Client, m: Message) -> None:
        """Print runtime diagnostics — useful when chasing platform issues
        (Railway, Fly, etc.)."""
        if not _is_admin(m):
            return
        try:
            import pyrogram as _pyro

            pyro_ver = getattr(_pyro, "__version__", "?")
        except Exception:  # noqa: BLE001
            pyro_ver = "?"
        env_keys = sorted(
            k for k in os.environ
            if k in {"BOT_TOKEN", "API_ID", "API_HASH", "ADMIN_ID",
                     "WORK_DIR", "DATABASE_URL", "PORT", "RAILWAY_PROJECT_ID",
                     "FLY_APP_NAME", "KOYEB_APP_NAME"}
        )
        await m.reply_text(
            "*🩺 Diagnostics*\n"
            f"Python: `{sys.version.split()[0]}`\n"
            f"Platform: `{platform.platform()}`\n"
            f"Pyrogram: `{pyro_ver}`\n"
            f"PID: `{os.getpid()}`\n"
            f"CWD: `{os.getcwd()}`\n"
            f"work_dir: `{settings.work_dir}` "
            f"(exists=`{settings.work_dir.exists()}`)\n"
            f"DB: `{settings.database_url}`\n"
            f"Health port: `{settings.port}`\n"
            f"Disk used: `{bytes_human(disk_used_bytes())}` / "
            f"`{bytes_human(settings.disk_budget_bytes)}` budget\n"
            f"FS free: `{bytes_human(free_disk_bytes())}`\n"
            f"Concurrency cap: `{settings.max_concurrent_jobs}` / "
            f"active=`{jobs.active_count}`\n"
            f"Env keys present: `{env_keys}`"
        )

    @app.on_message(filters.command(["cleanup"]))
    async def on_cleanup(_: Client, m: Message) -> None:
        if not _is_admin(m):
            return
        before = disk_used_bytes()
        for entry in settings.work_dir.iterdir():
            if entry.is_dir() and entry.name.startswith("job-"):
                shutil.rmtree(entry, ignore_errors=True)
            elif entry.is_file():
                try:
                    entry.unlink()
                except OSError:
                    pass
        after = disk_used_bytes()
        await m.reply_text(
            f"🧹 Freed `{bytes_human(max(0, before - after))}` "
            f"(was `{bytes_human(before)}`, now `{bytes_human(after)}`)."
        )

    @app.on_message(filters.command(["grant", "revokevip"]))
    async def on_vip(_: Client, m: Message) -> None:
        if not _is_admin(m):
            return
        parts = (m.text or "").split()
        if len(parts) < 2:
            await m.reply_text(f"Usage: `{parts[0] if parts else '/grant'} <user_id>`")
            return
        try:
            uid = int(parts[1])
        except ValueError:
            await m.reply_text("user_id must be a number.")
            return
        # Strip the optional `@botname` suffix Telegram appends in groups
        # so `/grant@mybot` and `/grant` are treated the same.
        cmd = parts[0].lstrip("/").split("@", 1)[0].lower()
        ok = await set_vip(uid, cmd == "grant")
        if not ok:
            await m.reply_text(
                "User not found in DB. They need to /start the bot at least once."
            )
            return
        verb = "granted ⭐ VIP to" if cmd == "grant" else "revoked VIP from"
        await m.reply_text(f"Done — {verb} `{uid}`.")

    @app.on_message(filters.command(["info"]))
    async def on_info(_: Client, m: Message) -> None:
        if not _is_admin(m):
            return
        parts = (m.text or "").split()
        if len(parts) < 2:
            await m.reply_text("Usage: `/info <user_id>`")
            return
        try:
            uid = int(parts[1])
        except ValueError:
            await m.reply_text("user_id must be a number.")
            return
        u = await get_user(uid)
        if u is None:
            await m.reply_text("No record for that user.")
            return
        tag = f"@{u.username}" if u.username else "(no username)"
        flags: list[str] = []
        if uid == settings.admin_id:
            flags.append("👑 admin")
        if u.is_vip:
            flags.append("⭐ vip")
        if u.is_banned:
            flags.append("🚫 banned")
        flag_line = " · ".join(flags) if flags else "👤 user"
        await m.reply_text(
            f"*ℹ️ User `{uid}`*\n"
            f"Name: `{u.first_name or '?'}` {tag}\n"
            f"Flags: {flag_line}\n"
            f"Member since: `{u.created_at:%Y-%m-%d %H:%M UTC}`\n"
            f"Total jobs: `{u.total_jobs}`\n"
            f"Total bytes: `{bytes_human(u.total_bytes)}`\n"
            f"Today: `{bytes_human(u.daily_used_bytes)}` / "
            f"`{bytes_human(settings.per_user_daily_bytes)}` "
            f"(reset `{u.daily_reset_at:%Y-%m-%d %H:%M UTC}`)"
        )

    @app.on_message(filters.command(["echo"]))
    async def on_echo(client: Client, m: Message) -> None:
        if not _is_admin(m):
            return
        parts = (m.text or "").split(maxsplit=2)
        if len(parts) < 3:
            await m.reply_text("Usage: `/echo <chat_id> <message…>`")
            return
        try:
            chat_id = int(parts[1])
        except ValueError:
            await m.reply_text("chat_id must be a number.")
            return
        try:
            await client.send_message(chat_id, parts[2])
            await m.reply_text(f"📨 Sent to `{chat_id}`.")
        except Exception as e:  # noqa: BLE001
            await m.reply_text(f"❌ Send failed: `{e}`")

    @app.on_message(filters.command(["restart"]))
    async def on_restart(_: Client, m: Message) -> None:
        if not _is_admin(m):
            return
        await m.reply_text("♻️ Restarting — see you in a few seconds.")
        log.warning("Admin %s requested /restart — exiting.", m.from_user.id)
        # Schedule the exit on the next event-loop tick so this reply gets
        # delivered before the process dies. The platform (Railway / Fly /
        # Docker --restart=always) brings us back automatically.
        loop = asyncio.get_running_loop()
        loop.call_later(0.5, lambda: os._exit(0))
