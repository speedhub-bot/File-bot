from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.db.repo import get_or_create_user
from bot.utils.format import bytes_human

log = logging.getLogger(__name__)

# A user-level mailbox of "messages awaiting a split decision". Keyed by user_id.
PENDING: dict[int, dict] = {}

# Filenames coming off Telegram messages are user-controlled, so they must be
# sanitized before being used as a filesystem path component.
_BAD_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')


def _safe_filename(raw: str | None, fallback: str) -> str:
    """Strip directory components and dangerous characters from a user-supplied
    filename. Falls back to ``fallback`` if nothing usable remains."""

    if not raw:
        return fallback
    # Drop anything that looks like a path component on either OS by
    # normalizing both separators and taking the basename.
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = _BAD_CHARS.sub("_", name).strip(" .")
    if not name or name in {".", ".."}:
        return fallback
    return name[:200]  # cap length to keep filesystem happy


def _file_meta(m: Message) -> tuple[str, int] | None:
    """Return (sanitized_filename, size) for any file-bearing message, or None."""
    if m.document:
        return _safe_filename(m.document.file_name, "file.bin"), m.document.file_size or 0
    if m.video:
        return _safe_filename(m.video.file_name, f"video-{m.id}.mp4"), m.video.file_size or 0
    if m.audio:
        return _safe_filename(m.audio.file_name, f"audio-{m.id}.mp3"), m.audio.file_size or 0
    if m.voice:
        return f"voice-{m.id}.ogg", m.voice.file_size or 0
    if m.video_note:
        return f"video-note-{m.id}.mp4", m.video_note.file_size or 0
    if m.animation:
        return _safe_filename(m.animation.file_name, f"anim-{m.id}.mp4"), m.animation.file_size or 0
    if m.photo:
        return f"photo-{m.id}.jpg", m.photo.file_size or 0
    if m.sticker:
        if m.sticker.is_animated:
            ext = "tgs"
        elif getattr(m.sticker, "is_video", False):
            ext = "webm"
        else:
            ext = "webp"
        return f"sticker-{m.id}.{ext}", m.sticker.file_size or 0
    return None


def _kb_for_file() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📦 By size", callback_data="split:size"),
                InlineKeyboardButton("#️⃣ By count", callback_data="split:count"),
            ],
            [
                InlineKeyboardButton("🤖 Auto split (~50 MB)", callback_data="split:auto"),
            ],
            [
                InlineKeyboardButton("⏩ No split", callback_data="split:none"),
                InlineKeyboardButton("❌ Cancel", callback_data="split:cancel"),
            ],
        ]
    )


def register(app: Client) -> None:
    media_filter = (
        filters.document
        | filters.video
        | filters.audio
        | filters.voice
        | filters.video_note
        | filters.animation
        | filters.photo
        | filters.sticker
    )

    @app.on_message(media_filter & ~filters.bot)
    async def on_file(client: Client, m: Message) -> None:
        u = m.from_user
        if not u:
            return
        user = await get_or_create_user(u.id, u.username, u.first_name)
        if user.is_banned:
            await m.reply_text("🚫 You are banned from using this bot.")
            return

        meta = _file_meta(m)
        if meta is None:
            await m.reply_text("Hmm, I can't see a file in that message.")
            return
        name, size = meta

        PENDING[u.id] = {
            "chat_id": m.chat.id,
            "src_message_id": m.id,
            "file_name": name,
            "file_size": size,
        }

        await m.reply_text(
            f"📁 *{name}*\n"
            f"Size: *{bytes_human(size) if size else 'unknown'}*\n\n"
            "How would you like me to handle it?",
            reply_markup=_kb_for_file(),
            quote=True,
        )

    @app.on_message(filters.command(["cancel"]))
    async def on_cancel(_: Client, m: Message) -> None:
        if m.from_user and PENDING.pop(m.from_user.id, None) is not None:
            await m.reply_text("Cancelled.")
        else:
            await m.reply_text("Nothing pending.")
