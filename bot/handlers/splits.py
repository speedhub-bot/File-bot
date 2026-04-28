from __future__ import annotations

import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, ForceReply, Message

from bot.handlers.files import PENDING
from bot.services.jobs import JobRequest, jobs
from bot.utils.format import parse_count, parse_size

log = logging.getLogger(__name__)

# user_id -> {"mode": "size"|"count", "prompt_msg_id": int}
AWAITING: dict[int, dict] = {}


def register(app: Client) -> None:
    @app.on_callback_query(filters.regex(r"^split:(size|count|auto|none|cancel)$"))
    async def on_split_choice(client: Client, cb: CallbackQuery) -> None:
        u = cb.from_user
        if not u or not cb.message:
            return
        action = cb.data.split(":", 1)[1]
        pending = PENDING.get(u.id)
        if not pending:
            await cb.answer("Nothing pending — send me a file first.", show_alert=True)
            return

        if action == "cancel":
            PENDING.pop(u.id, None)
            AWAITING.pop(u.id, None)
            await cb.answer("Cancelled")
            try:
                await cb.message.edit_reply_markup()
            except Exception:
                pass
            return

        if action in {"auto", "none"}:
            PENDING.pop(u.id, None)
            try:
                await cb.message.edit_reply_markup()
            except Exception:
                pass
            await cb.answer("Starting…")
            asyncio.create_task(  # noqa: RUF006
                jobs.submit(
                    client,
                    JobRequest(
                        user_id=u.id,
                        chat_id=pending["chat_id"],
                        src_message_id=pending["src_message_id"],
                        file_name=pending["file_name"],
                        file_size=pending["file_size"],
                        mode=action,
                    ),
                )
            )
            return

        # action in {"size", "count"} — ask the user for the value.
        prompt = await client.send_message(
            cb.message.chat.id,
            (
                "Reply with the **part size** (e.g. `100 MB`, `1.5 GB`)."
                if action == "size"
                else "Reply with the **number of parts** (e.g. `20`)."
            ),
            reply_markup=ForceReply(selective=True),
        )
        AWAITING[u.id] = {"mode": action, "prompt_msg_id": prompt.id}
        await cb.answer()

    @app.on_message(filters.text & filters.private & ~filters.command(["start", "help", "cancel"]))
    async def on_text_reply(client: Client, m: Message) -> None:
        u = m.from_user
        if not u:
            return
        await_state = AWAITING.get(u.id)
        if not await_state:
            return
        # Only accept the value if it's a reply to our ForceReply prompt.
        if not m.reply_to_message or m.reply_to_message.id != await_state["prompt_msg_id"]:
            return

        pending = PENDING.get(u.id)
        if not pending:
            await m.reply_text("Hmm, the file slot expired — please send the file again.")
            AWAITING.pop(u.id, None)
            return

        try:
            if await_state["mode"] == "size":
                value = parse_size(m.text)
                if value < 32 * 1024:
                    await m.reply_text("Part size must be at least 32 KB.")
                    return
            else:
                value = parse_count(m.text)
                if value > 5000:
                    await m.reply_text("Too many parts — please pick ≤ 5000.")
                    return
        except ValueError as e:
            await m.reply_text(f"Couldn't parse that: `{e}`. Try again or /cancel.")
            return

        AWAITING.pop(u.id, None)
        PENDING.pop(u.id, None)

        asyncio.create_task(  # noqa: RUF006
            jobs.submit(
                client,
                JobRequest(
                    user_id=u.id,
                    chat_id=pending["chat_id"],
                    src_message_id=pending["src_message_id"],
                    file_name=pending["file_name"],
                    file_size=pending["file_size"],
                    mode=await_state["mode"],
                    value=value,
                ),
            )
        )
