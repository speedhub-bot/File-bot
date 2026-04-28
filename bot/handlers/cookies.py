from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import settings
from bot.db.repo import add_quota_used
from bot.services.cookies import run_extraction
from bot.services.quota import QuotaError, assert_can_accept
from bot.utils.format import bytes_human

log = logging.getLogger(__name__)

# user_id -> {"prompt_msg_id": int}
AWAITING_FILE: dict[int, dict] = {}
# user_id -> {"temp_dir": Path, "file_path": Path, "prompt_msg_id": int, "progress_msg_id": int}
AWAITING_DOMAIN: dict[int, dict] = {}

def register(app: Client) -> None:
    @app.on_callback_query(filters.regex(r"^cookies:start$"))
    async def on_cookies_btn(client: Client, cb: CallbackQuery) -> None:
        u = cb.from_user
        if not u:
            return

        text = (
            "🍪 **Smart Cookie Extractor**\n\n"
            "I can extract cookies for a specific domain from your log files. "
            "I support `.txt` files, and archives like `.zip`, `.7z`. (Note: `.rar` requires server-side support).\n\n"
            "**How it works:**\n"
            "1. Send me your log file or archive.\n"
            "2. I'll ask for the domain (e.g., `youtube.com`).\n"
            "3. I'll search through all files and find matching cookies.\n"
            "4. I'll send you a ZIP file with all cookies organized by source.\n\n"
            "Please **send or forward** your log file now."
        )

        await cb.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="cookies:cancel")
            ]])
        )
        AWAITING_FILE[u.id] = {"prompt_msg_id": cb.message.id}
        await cb.answer()

    @app.on_callback_query(filters.regex(r"^cookies:cancel$"))
    async def on_cookies_cancel(client: Client, cb: CallbackQuery) -> None:
        u = cb.from_user
        if not u:
            return
        AWAITING_FILE.pop(u.id, None)
        state = AWAITING_DOMAIN.pop(u.id, None)
        if state:
            shutil.rmtree(state["temp_dir"], ignore_errors=True)

        await cb.message.edit_text("❌ Cookie extraction cancelled.")
        await cb.answer()

    # Filter for any file during AWAITING_FILE state
    # group=-1 to ensure we intercept it before the general file splitter
    @app.on_message(filters.private & (filters.document | filters.audio | filters.video | filters.animation) & ~filters.command(["start", "help", "cancel"]), group=-1)
    async def on_log_file(client: Client, m: Message) -> None:
        u = m.from_user
        if not u or u.id not in AWAITING_FILE:
            return

        # Check quota
        file_size = 0
        file_name = "logs"
        if m.document:
            file_size = m.document.file_size or 0
            file_name = m.document.file_name or "logs.bin"
        elif m.audio:
            file_size = m.audio.file_size or 0
            file_name = m.audio.file_name or "logs.mp3"
        elif m.video:
            file_size = m.video.file_size or 0
            file_name = m.video.file_name or "logs.mp4"

        try:
            await assert_can_accept(u.id, file_size)
        except QuotaError as e:
            await m.reply_text(f"❌ {e}")
            return

        # Prepare temp dir
        temp_dir = settings.work_dir / f"cookies-{u.id}-{uuid.uuid4().hex[:8]}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        progress_msg = await m.reply_text("📥 **Downloading logs…**", quote=True)

        try:
            file_path = await client.download_media(m, file_name=str(temp_dir / file_name))
            if not file_path:
                raise RuntimeError("Download failed")

            AWAITING_FILE.pop(u.id, None)
            m.stop_propagation()  # Stop other handlers from seeing this file
            prompt = await m.reply_text(
                "✅ Logs received.\n\nNow send me the **domain** you want to extract cookies for (e.g., `spotify.com`).",
                quote=True,
            )
            AWAITING_DOMAIN[u.id] = {
                "temp_dir": temp_dir,
                "file_path": Path(file_path),
                "prompt_msg_id": prompt.id,
                "progress_msg_id": progress_msg.id,
            }
        except Exception as e:
            log.exception("Failed to handle log file")
            AWAITING_FILE.pop(u.id, None)
            await progress_msg.edit_text(f"❌ Failed to download file: `{e}`")
            shutil.rmtree(temp_dir, ignore_errors=True)
            m.stop_propagation()  # Don't let the file fall through to the split handler

    @app.on_message(filters.private & filters.text & ~filters.command(["start", "help", "cancel"]), group=-1)
    async def on_domain_reply(client: Client, m: Message) -> None:
        u = m.from_user
        if not u or u.id not in AWAITING_DOMAIN:
            return

        domain = m.text.strip().lower()
        if "." not in domain:
            await m.reply_text(
                "❌ Invalid domain. Please send a valid domain like `youtube.com`.",
            )
            m.stop_propagation()
            return

        state = AWAITING_DOMAIN.pop(u.id)
        m.stop_propagation()  # Stop other handlers

        progress_msg_id = state["progress_msg_id"]
        temp_dir = state["temp_dir"]
        file_path = state["file_path"]

        await client.edit_message_text(m.chat.id, progress_msg_id, f"🔍 **Extracting cookies for `{domain}`…**")

        # Run extraction in background
        asyncio.create_task(do_extraction(client, m.chat.id, u.id, progress_msg_id, temp_dir, file_path, domain))

async def do_extraction(client, chat_id, user_id, progress_msg_id, temp_dir, file_path, domain):
    try:
        result_zip = await run_extraction(file_path, domain, temp_dir)

        if result_zip and result_zip.exists():
            size = result_zip.stat().st_size
            await client.send_document(
                chat_id,
                document=str(result_zip),
                caption=f"🍪 **Cookies for `{domain}`**\nSize: `{bytes_human(size)}`",
            )
            await client.edit_message_text(chat_id, progress_msg_id, "✅ **Extraction complete!**")
            await add_quota_used(user_id, file_path.stat().st_size)
        else:
            await client.edit_message_text(chat_id, progress_msg_id, f"❌ No cookies found for `{domain}` in the provided logs.")

    except Exception as e:
        log.exception("Extraction failed")
        await client.send_message(chat_id, f"❌ Extraction failed: `{e}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
