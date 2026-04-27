from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from bot.db.repo import get_or_create_user

WELCOME = (
    "👋 *Welcome to File Bot!*\n\n"
    "Forward me *any* file (document, video, audio, photo, voice, archive…) and I'll:\n"
    "• download it for you\n"
    "• optionally split it into smart parts (binary slices for media, "
    "*line/word-aware* slices for text/code)\n"
    "• send each part back to you\n\n"
    "Files up to *2 GB* are supported via MTProto.\n\n"
    "Try /help to see all commands."
)

HELP = (
    "*Commands*\n"
    "/start — show welcome\n"
    "/help — show this help\n"
    "/cancel — cancel any pending split prompt\n\n"
    "*How splitting works*\n"
    "After you send a file I show four buttons:\n"
    "• *📦 By size* — type a size like `100 MB` or `1.5 GB`.\n"
    "• *#️⃣ By count* — type a number like `20`.\n"
    "• *🤖 Auto* — sliced into ~50 MB parts.\n"
    "• *⏩ No split* — re-upload as one file.\n\n"
    "Text/code files (`.txt`, `.csv`, `.json`, `.py`, …) are split on the nearest "
    "line/word boundary so no sentence is cut mid-word. Binary files are split at "
    "exact byte offsets.\n\n"
    "Each part is uploaded and *immediately deleted* from disk so the bot stays "
    "well within Railway's 1 GB free-tier volume."
)


def register(app: Client) -> None:
    @app.on_message(filters.command(["start"]) & filters.private)
    async def on_start(_: Client, m: Message) -> None:
        u = m.from_user
        if u:
            await get_or_create_user(u.id, u.username, u.first_name)
        await m.reply_text(WELCOME, disable_web_page_preview=True)

    @app.on_message(filters.command(["help"]))
    async def on_help(_: Client, m: Message) -> None:
        await m.reply_text(HELP, disable_web_page_preview=True)
