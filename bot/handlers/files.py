from __future__ import annotations

import logging

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


def _file_meta(m: Message) -> tuple[str, int] | None:
    """Return (filename, size) for any file-bearing message, or None."""
    if m.document:
        return m.document.file_name or "file.bin", m.document.file_size or 0
    if m.video:
        return m.video.file_name or f"video-{m.id}.mp4", m.video.file_size or 0
    if m.audio:
        return m.audio.file_name or f"audio-{m.id}.mp3", m.audio.file_size or 0
    if m.voice:
        return f"voice-{m.id}.ogg", m.voice.file_size or 0
    if m.video_note:
        return f"video-note-{m.id}.mp4", m.video_note.file_size or 0
    if m.animation:
        return m.animation.file_name or f"anim-{m.id}.mp4", m.animation.file_size or 0
    if m.photo:
        return f"photo-{m.id}.jpg", m.photo.file_size or 0
    if m.sticker:
        ext = "webp" if not m.sticker.is_animated else "tgs"
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
        await get_or_create_user(u.id, u.username, u.first_name)

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
