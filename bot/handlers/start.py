from __future__ import annotations

from datetime import datetime, timedelta

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import settings
from bot.db.repo import get_or_create_user, reset_quota_if_needed
from bot.utils.format import bytes_human

# ---------------------------------------------------------------------------
# Credits
#
# The bot was set up at the request of @speedhub-bot but the original idea +
# concept comes from @akaza_isnt — the credit line below is rendered into
# /start, /help and /profile so attribution is always visible.
#
# Pyrogram 2 markdown treats a single `_` as literal text (only doubled
# `__…__` is italic), so the underscore in `@akaza_isnt` doesn't need
# escaping. We also wrap it in an inline link so taps open the channel.
# ---------------------------------------------------------------------------
CREDIT_LINE = "🪪 __Bot by__ [@akaza_isnt](https://t.me/akaza_isnt)"


def _welcome(name: str | None) -> str:
    who = name or "there"
    return (
        f"👋 **Hey {who}!**\n\n"
        "I'm **File Bot** — forward me __any__ file (document, video, audio, "
        "voice, sticker, archive, …) and I'll:\n"
        "• stream-download it (no 20 MB Bot-API ceiling)\n"
        "• split it __smartly__ — by size, by count, or auto\n"
        "• keep text/code on line boundaries so nothing is cut mid-word\n"
        "• send each part back and free disk as I go\n\n"
        "Files up to **2 GB** per message are supported.\n\n"
        "Tap **📖 Help** below or run /help to learn every command.\n\n"
        f"{CREDIT_LINE}"
    )


HELP_HOME = (
    "**📖 Help — pick a topic**\n\n"
    "• **Files** — forward a file, choose how to split it.\n"
    "• **URL ingest** — `/url <link>` to pull from the web.\n"
    "• **Merge** — re-join parts back into the original file.\n"
    "• **Profile** — your stats, quota and VIP status.\n"
    "• **Queue** — how concurrency works.\n"
    "• **Admin** — operator-only commands.\n\n"
    f"{CREDIT_LINE}"
)

HELP_FILES = (
    "**📁 Files & splitting**\n\n"
    "Forward me any file in a private chat. I'll show four buttons:\n"
    "• **📦 By size** — type a size like `100 MB` or `1.5 GB` and I'll cut at "
    "those byte offsets.\n"
    "• **#️⃣ By count** — type a number like `20` and I'll make 20 evenly-sized "
    "parts.\n"
    "• **🤖 Auto** — sliced into ~50 MB parts (good default for most uploads).\n"
    "• **⏩ No split** — re-upload as a single file.\n\n"
    "**Smart splitting:** `.txt` `.csv` `.json` `.py` `.md` `.log` etc. are cut "
    "on the nearest line/word boundary so no sentence breaks mid-word. "
    "Binary files are split at exact byte offsets.\n\n"
    "Each part is uploaded then **immediately deleted** from disk — works fine "
    "on Railway's 1 GB free volume."
)

HELP_URL = (
    "**🌐 URL ingest**\n\n"
    "Use `/url <https-link>` to make me pull a file directly off the web "
    "without you having to download it first.\n\n"
    "I do a HEAD probe before fetching so I can quota-check up front. "
    "Servers that send chunked transfers without a `Content-Length` header "
    "are still supported — I just skip the up-front size check.\n\n"
    "After download you'll get the same split-mode picker as forwarded files."
)

HELP_MERGE = (
    "**🧷 Merge — rebuild the original file**\n\n"
    "Workflow:\n"
    "1. `/merge start` — opens a session.\n"
    "2. Forward me the parts (`*.part-NN.ext`) in any order; I'll match them "
    "by index.\n"
    "3. `/merge status` — shows what I've got so far.\n"
    "4. `/merge done <output-name>` — joins everything and sends it back.\n\n"
    "If parts are missing I'll warn once. Run `/merge done` again and I'll "
    "merge what's there. `/merge cancel` aborts."
)

HELP_PROFILE = (
    "**👤 Profile commands**\n\n"
    "• /profile — your stats card: jobs run, bytes processed, daily quota "
    "left, member since, and VIP status.\n"
    "• /cancel — drop a pending split prompt.\n\n"
    "**Quota:** every account has a daily byte allowance. VIPs (and the admin) "
    "have it disabled — ask the admin to `/grant` you VIP."
)

HELP_QUEUE = (
    "**🔒 Queue — one user at a time**\n\n"
    "To keep this bot stable on a 1 GB volume, only **one** non-VIP job runs "
    "at a time. If you forward a file while someone else is converting "
    "you'll get a **⏳ queued** message — I'll start automatically when the "
    "previous job finishes.\n\n"
    "**Admin and VIPs bypass this gate** and can run jobs in parallel. The "
    "admin can promote anyone to VIP via `/grant <user_id>`."
)

HELP_ADMIN = (
    "**🛠 Admin commands**\n\n"
    "**Operations**\n"
    "• /stats — counters, disk usage, budget remaining\n"
    "• /jobs — active + recent job list\n"
    "• /users — top users by bytes\n"
    "• /diag — runtime diagnostics\n"
    "• /cleanup — purge `work_dir/job-*`\n"
    "• /restart — exit(0); platform restarts the container\n\n"
    "**Moderation**\n"
    "• /ban `<id>` and /unban `<id>`\n"
    "• /grant `<id>` — promote to VIP (no daily quota, bypasses queue)\n"
    "• /revokevip `<id>` — demote\n"
    "• /info `<id>` — full record for a user\n\n"
    "**Outreach**\n"
    "• /broadcast `<text>` — DM every non-banned user\n"
    "• /echo `<chat_id>` `<text>` — DM a single chat as the bot"
)

_HELP_PAGES = {
    "home": HELP_HOME,
    "files": HELP_FILES,
    "url": HELP_URL,
    "merge": HELP_MERGE,
    "profile": HELP_PROFILE,
    "queue": HELP_QUEUE,
    "admin": HELP_ADMIN,
}


def _help_kb(current: str = "home") -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📁 Files", callback_data="help:files"),
            InlineKeyboardButton("🌐 URL", callback_data="help:url"),
            InlineKeyboardButton("🧷 Merge", callback_data="help:merge"),
        ],
        [
            InlineKeyboardButton("👤 Profile", callback_data="help:profile"),
            InlineKeyboardButton("🔒 Queue", callback_data="help:queue"),
            InlineKeyboardButton("🛠 Admin", callback_data="help:admin"),
        ],
    ]
    if current != "home":
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="help:home")])
    return InlineKeyboardMarkup(rows)


def _start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📖 Help", callback_data="help:home"),
                InlineKeyboardButton("👤 Profile", callback_data="profile:me"),
            ]
        ]
    )


async def _profile_text(user_id: int, display_name: str) -> str:
    u = await reset_quota_if_needed(user_id)
    is_admin = user_id == settings.admin_id
    role = "👑 Admin" if is_admin else ("⭐ VIP" if u.is_vip else "👤 User")
    quota_total = settings.per_user_daily_bytes
    if is_admin or u.is_vip:
        quota_line = "Daily quota: **unlimited**"
    elif quota_total > 0:
        remaining = max(0, quota_total - u.daily_used_bytes)
        quota_line = (
            f"Daily quota: `{bytes_human(u.daily_used_bytes)}` of "
            f"`{bytes_human(quota_total)}` used "
            f"(`{bytes_human(remaining)}` left)"
        )
    else:
        quota_line = "Daily quota: **disabled**"
    next_reset = u.daily_reset_at + timedelta(days=1)
    until_reset = max(timedelta(seconds=0), next_reset - datetime.utcnow())
    h, rem = divmod(int(until_reset.total_seconds()), 3600)
    mm = rem // 60
    return (
        f"**👤 {display_name}**\n"
        f"ID: `{user_id}`\n"
        f"Role: {role}\n"
        f"Member since: `{u.created_at:%Y-%m-%d}`\n\n"
        f"Total jobs: `{u.total_jobs}`\n"
        f"Total processed: `{bytes_human(u.total_bytes)}`\n"
        f"{quota_line}\n"
        f"Quota resets in: `{h:02d}h {mm:02d}m`\n\n"
        f"{CREDIT_LINE}"
    )


def register(app: Client) -> None:
    @app.on_message(filters.command(["start"]) & filters.private)
    async def on_start(_: Client, m: Message) -> None:
        u = m.from_user
        if u:
            await get_or_create_user(u.id, u.username, u.first_name)
        await m.reply_text(
            _welcome(u.first_name if u else None),
            reply_markup=_start_kb(),
            disable_web_page_preview=True,
        )

    @app.on_message(filters.command(["help"]))
    async def on_help(_: Client, m: Message) -> None:
        await m.reply_text(
            HELP_HOME, reply_markup=_help_kb("home"), disable_web_page_preview=True
        )

    @app.on_callback_query(filters.regex(r"^help:"))
    async def on_help_cb(_: Client, q: CallbackQuery) -> None:
        page = q.data.split(":", 1)[1]
        body = _HELP_PAGES.get(page, HELP_HOME)
        try:
            await q.message.edit_text(body, reply_markup=_help_kb(page))
        except Exception:  # noqa: BLE001
            # MESSAGE_NOT_MODIFIED etc. — harmless.
            pass
        await q.answer()

    @app.on_message(filters.command(["profile", "me"]))
    async def on_profile(_: Client, m: Message) -> None:
        u = m.from_user
        if not u:
            return
        await get_or_create_user(u.id, u.username, u.first_name)
        text = await _profile_text(u.id, u.first_name or u.username or "User")
        await m.reply_text(text, disable_web_page_preview=True)

    @app.on_callback_query(filters.regex(r"^profile:me$"))
    async def on_profile_cb(_: Client, q: CallbackQuery) -> None:
        u = q.from_user
        await get_or_create_user(u.id, u.username, u.first_name)
        text = await _profile_text(u.id, u.first_name or u.username or "User")
        try:
            await q.message.edit_text(text, reply_markup=_help_kb("home"))
        except Exception:  # noqa: BLE001
            pass
        await q.answer()
