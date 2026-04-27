from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import settings
from bot.db.repo import get_or_create_user
from bot.handlers.files import PENDING, _safe_filename
from bot.services.quota import QuotaError, assert_can_accept
from bot.utils.format import bytes_human, progress_bar

log = logging.getLogger(__name__)

# Stream chunk size for URL downloads.
HTTP_CHUNK = 1024 * 1024  # 1 MiB
HTTP_TIMEOUT = httpx.Timeout(30.0, read=300.0)
MAX_REDIRECTS = 5


def _kb_for_url() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📦 By size", callback_data="split:size"),
                InlineKeyboardButton("#️⃣ By count", callback_data="split:count"),
            ],
            [InlineKeyboardButton("🤖 Auto split (~50 MB)", callback_data="split:auto")],
            [
                InlineKeyboardButton("⏩ No split", callback_data="split:none"),
                InlineKeyboardButton("❌ Cancel", callback_data="split:cancel"),
            ],
        ]
    )


def _filename_from_url(url: str, fallback_id: int) -> str:
    parsed = urlparse(url)
    raw = unquote(Path(parsed.path).name)
    return _safe_filename(raw, f"download-{fallback_id}.bin")


async def _stream_to_disk(
    url: str,
    target: Path,
    on_progress,
) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    written = 0
    last_edit = 0.0
    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT, follow_redirects=True, max_redirects=MAX_REDIRECTS
    ) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            try:
                total = int(resp.headers.get("content-length", "0"))
            except ValueError:
                total = 0
            with target.open("wb") as fh:
                async for chunk in resp.aiter_bytes(HTTP_CHUNK):
                    fh.write(chunk)
                    written += len(chunk)
                    now = time.monotonic()
                    if now - last_edit >= 1.0:
                        last_edit = now
                        await on_progress(written, total)
    return written


def register(app: Client) -> None:
    @app.on_message(filters.command(["url"]) & filters.private)
    async def on_url(client: Client, m: Message) -> None:
        u = m.from_user
        if not u:
            return
        user = await get_or_create_user(u.id, u.username, u.first_name)
        if user.is_banned:
            await m.reply_text("🚫 You are banned from using this bot.")
            return

        # Pull the URL from the command arguments.
        rest = (m.text or "").split(maxsplit=1)
        if len(rest) < 2:
            await m.reply_text(
                "Usage: `/url https://example.com/file.zip`\n\n"
                "I'll stream-download it (1 MiB chunks, never buffers in RAM) "
                "and then ask you how to split it."
            )
            return
        url = rest[1].strip()
        if not url.startswith(("http://", "https://")):
            await m.reply_text("Only `http://` and `https://` URLs are accepted.")
            return

        # HEAD probe so we can quota-check before pulling bytes.
        try:
            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT, follow_redirects=True, max_redirects=MAX_REDIRECTS
            ) as c:
                head = await c.head(url)
            head.raise_for_status()
            content_length = int(head.headers.get("content-length", "0"))
        except httpx.HTTPError as e:
            await m.reply_text(f"❌ Couldn't reach that URL: `{e}`")
            return

        if content_length:
            try:
                await assert_can_accept(u.id, content_length)
            except QuotaError as e:
                await m.reply_text(f"❌ {e}")
                return

        name = _filename_from_url(url, m.id)
        # Stash this in a private "url-ingest" working slot. We download here
        # synchronously (within an async task) and then hand off to the same
        # split flow used by forwarded files.
        progress_msg = await m.reply_text(f"🌐 Fetching `{name}`…")
        target = settings.work_dir / f"url-{u.id}-{uuid.uuid4().hex[:8]}" / name

        async def _on_progress(done: int, total: int) -> None:
            text = (
                f"🌐 *Downloading from URL*\n`{progress_bar(done, total)}`\n"
                f"{bytes_human(done)} / {bytes_human(total) if total else '?'}"
            )
            try:
                await client.edit_message_text(m.chat.id, progress_msg.id, text)
            except Exception:  # noqa: BLE001
                pass

        try:
            actual = await _stream_to_disk(url, target, _on_progress)
        except httpx.HTTPError as e:
            await client.edit_message_text(
                m.chat.id, progress_msg.id, f"❌ Download failed: `{e}`"
            )
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            return

        # Re-upload to the user as a normal Telegram document so the *split
        # decision* flow can treat it like any other forwarded file.
        await client.edit_message_text(
            m.chat.id, progress_msg.id, "📤 *Forwarding to Telegram…*"
        )
        sent = await client.send_document(
            m.chat.id,
            document=str(target),
            caption=f"`{name}` ({bytes_human(actual)})",
        )
        try:
            target.unlink(missing_ok=True)
            target.parent.rmdir()
        except OSError:
            pass

        # Now offer the same split keyboard the normal /file flow uses.
        PENDING[u.id] = {
            "chat_id": m.chat.id,
            "src_message_id": sent.id,
            "file_name": name,
            "file_size": actual,
        }
        await client.send_message(
            m.chat.id,
            f"📁 *{name}*\nSize: *{bytes_human(actual)}*\n\nHow should I split it?",
            reply_markup=_kb_for_url(),
        )

        # Best-effort cleanup of the progress message.
        await asyncio.sleep(0)
