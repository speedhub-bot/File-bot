"""Request-based access + admin panel.

Replaces the old text-based admin commands (/grant /revokevip /ban /unban
/info /diag /stats /jobs /users /broadcast /echo /cleanup /restart) with a
single inline-button workflow:

User flow
---------
1. User runs /start. Welcome message includes a "🔓 Request VIP access" button.
2. Tapping it records `requested_at` and DMs the admin a notification card
   with [✅ Approve] [🚫 Ban] inline buttons.
3. The user's own message updates to "✅ Request sent — admin notified".

Admin flow
----------
- Admin gets the notification DM and taps Approve or Ban; both are persistent
  (set is_vip / is_banned in the DB).
- Admin can also run /panel to open a paged list of pending / approved /
  banned users at any time, each row carrying a Revoke or Unban button so
  decisions are reversible.

Approved == VIP perks (no daily quota, bypass single-user queue). Unapproved
users keep using the bot at normal-user limits.
"""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import settings
from bot.db.repo import (
    clear_request,
    get_or_create_user,
    list_approved,
    list_banned,
    list_pending_requests,
    record_access_request,
    set_banned,
    set_vip,
)

log = logging.getLogger(__name__)

_PANEL_PAGE_SIZE = 10


def _user_tag(username: str | None, first_name: str | None, user_id: int) -> str:
    if username:
        return f"@{username}"
    if first_name:
        return first_name
    return f"user `{user_id}`"


def _request_button() -> InlineKeyboardButton:
    """Used by start.py inside the welcome keyboard."""
    return InlineKeyboardButton("🔓 Request VIP access", callback_data="access:request")


def _admin_review_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Approve", callback_data=f"access:approve:{user_id}"
                ),
                InlineKeyboardButton(
                    "🚫 Ban", callback_data=f"access:ban:{user_id}"
                ),
            ]
        ]
    )


def _panel_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📥 Pending", callback_data="panel:pending"),
                InlineKeyboardButton("⭐ Approved", callback_data="panel:approved"),
                InlineKeyboardButton("🚫 Banned", callback_data="panel:banned"),
            ],
            [InlineKeyboardButton("🔄 Refresh", callback_data="panel:home")],
        ]
    )


def _panel_back_kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="panel:home")])
    return InlineKeyboardMarkup(rows)


async def _render_pending() -> tuple[str, InlineKeyboardMarkup]:
    pending = await list_pending_requests(limit=_PANEL_PAGE_SIZE)
    if not pending:
        return (
            "**📥 Pending requests**\n\n__No pending requests.__",
            _panel_back_kb([]),
        )
    lines = ["**📥 Pending requests**", ""]
    rows: list[list[InlineKeyboardButton]] = []
    for u in pending:
        tag = _user_tag(u.username, u.first_name, u.user_id)
        lines.append(f"• {tag} (`{u.user_id}`)")
        rows.append(
            [
                InlineKeyboardButton(
                    f"✅ {tag}", callback_data=f"access:approve:{u.user_id}"
                ),
                InlineKeyboardButton(
                    "🚫", callback_data=f"access:ban:{u.user_id}"
                ),
            ]
        )
    return "\n".join(lines), _panel_back_kb(rows)


async def _render_approved() -> tuple[str, InlineKeyboardMarkup]:
    approved = await list_approved(limit=_PANEL_PAGE_SIZE)
    if not approved:
        return (
            "**⭐ Approved users**\n\n__No approved users yet.__",
            _panel_back_kb([]),
        )
    lines = ["**⭐ Approved users**", ""]
    rows: list[list[InlineKeyboardButton]] = []
    for u in approved:
        tag = _user_tag(u.username, u.first_name, u.user_id)
        lines.append(f"• {tag} (`{u.user_id}`)")
        rows.append(
            [
                InlineKeyboardButton(
                    f"⛔ Revoke {tag}",
                    callback_data=f"access:revoke:{u.user_id}",
                )
            ]
        )
    return "\n".join(lines), _panel_back_kb(rows)


async def _render_banned() -> tuple[str, InlineKeyboardMarkup]:
    banned = await list_banned(limit=_PANEL_PAGE_SIZE)
    if not banned:
        return (
            "**🚫 Banned users**\n\n__No banned users.__",
            _panel_back_kb([]),
        )
    lines = ["**🚫 Banned users**", ""]
    rows: list[list[InlineKeyboardButton]] = []
    for u in banned:
        tag = _user_tag(u.username, u.first_name, u.user_id)
        lines.append(f"• {tag} (`{u.user_id}`)")
        rows.append(
            [
                InlineKeyboardButton(
                    f"♻️ Unban {tag}",
                    callback_data=f"access:unban:{u.user_id}",
                )
            ]
        )
    return "\n".join(lines), _panel_back_kb(rows)


def _is_admin_uid(uid: int | None) -> bool:
    return uid is not None and uid == settings.admin_id


def register(app: Client) -> None:
    # ------------------------------------------------------------------
    # User: tap "Request VIP access".
    # ------------------------------------------------------------------
    @app.on_callback_query(filters.regex(r"^access:request$"))
    async def on_request(client: Client, q: CallbackQuery) -> None:
        u = q.from_user
        if u is None:
            await q.answer()
            return
        if _is_admin_uid(u.id):
            await q.answer("You're the admin — no need to request.", show_alert=True)
            return
        row = await get_or_create_user(u.id, u.username, u.first_name)
        if row.is_banned:
            await q.answer("You are banned.", show_alert=True)
            return
        if row.is_vip:
            await q.answer("You're already approved as ⭐VIP.", show_alert=True)
            return
        if row.requested_at is not None:
            await q.answer(
                "Request already pending — please wait for admin review.",
                show_alert=True,
            )
            return
        await record_access_request(u.id)
        tag = _user_tag(u.username, u.first_name, u.id)
        try:
            await client.send_message(
                settings.admin_id,
                "🔔 **New access request**\n\n"
                f"From: {tag}\n"
                f"User ID: `{u.id}`\n\n"
                "Tap below to approve or ban.",
                reply_markup=_admin_review_kb(u.id),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("could not DM admin about request from %s: %s", u.id, e)
        try:
            await q.message.edit_text(
                "✅ **Request sent.**\n\n"
                "Admin has been notified. You'll get a DM here when they review it.",
            )
        except Exception:  # noqa: BLE001
            pass
        await q.answer("Request sent!")

    # ------------------------------------------------------------------
    # Admin: approve / ban / revoke / unban callbacks.
    # ------------------------------------------------------------------
    async def _admin_only(q: CallbackQuery) -> bool:
        if _is_admin_uid(q.from_user.id if q.from_user else None):
            return True
        await q.answer("Admin only.", show_alert=True)
        return False

    @app.on_callback_query(filters.regex(r"^access:approve:(\d+)$"))
    async def on_approve(client: Client, q: CallbackQuery) -> None:
        if not await _admin_only(q):
            return
        uid = int(q.matches[0].group(1))
        await set_banned(uid, False)
        await set_vip(uid, True)
        await clear_request(uid)
        try:
            await q.message.edit_text(
                f"✅ Approved `{uid}` as ⭐VIP.\n"
                "(Daily quota disabled, queue bypass enabled.)"
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            await client.send_message(
                uid,
                "✅ **Access approved.**\n\n"
                "You're now a ⭐VIP — daily quota disabled, queue bypass enabled. "
                "Forward me any file to start.",
            )
        except Exception as e:  # noqa: BLE001
            log.info("could not DM approval to %s: %s", uid, e)
        await q.answer("Approved")

    @app.on_callback_query(filters.regex(r"^access:ban:(\d+)$"))
    async def on_ban_cb(client: Client, q: CallbackQuery) -> None:
        if not await _admin_only(q):
            return
        uid = int(q.matches[0].group(1))
        await set_vip(uid, False)
        await set_banned(uid, True)
        await clear_request(uid)
        try:
            await q.message.edit_text(f"🚫 Banned `{uid}`.")
        except Exception:  # noqa: BLE001
            pass
        try:
            await client.send_message(uid, "🚫 You've been banned by the admin.")
        except Exception as e:  # noqa: BLE001
            log.info("could not DM ban to %s: %s", uid, e)
        await q.answer("Banned")

    @app.on_callback_query(filters.regex(r"^access:revoke:(\d+)$"))
    async def on_revoke(client: Client, q: CallbackQuery) -> None:
        if not await _admin_only(q):
            return
        uid = int(q.matches[0].group(1))
        await set_vip(uid, False)
        await clear_request(uid)
        try:
            await client.send_message(
                uid, "ℹ️ Your ⭐VIP access has been revoked by the admin."
            )
        except Exception:  # noqa: BLE001
            pass
        text, kb = await _render_approved()
        try:
            await q.message.edit_text(text, reply_markup=kb)
        except Exception:  # noqa: BLE001
            pass
        await q.answer("Revoked")

    @app.on_callback_query(filters.regex(r"^access:unban:(\d+)$"))
    async def on_unban_cb(client: Client, q: CallbackQuery) -> None:
        if not await _admin_only(q):
            return
        uid = int(q.matches[0].group(1))
        await set_banned(uid, False)
        try:
            await client.send_message(uid, "♻️ You've been unbanned by the admin.")
        except Exception:  # noqa: BLE001
            pass
        text, kb = await _render_banned()
        try:
            await q.message.edit_text(text, reply_markup=kb)
        except Exception:  # noqa: BLE001
            pass
        await q.answer("Unbanned")

    # ------------------------------------------------------------------
    # Admin: /panel command + panel-navigation callbacks.
    # ------------------------------------------------------------------
    @app.on_message(filters.command(["panel"]) & filters.private)
    async def on_panel(_: Client, m: Message) -> None:
        if not _is_admin_uid(m.from_user.id if m.from_user else None):
            return
        await m.reply_text(
            "**🛠 Admin panel**\n\nPick a section:",
            reply_markup=_panel_home_kb(),
        )

    @app.on_callback_query(filters.regex(r"^panel:(home|pending|approved|banned)$"))
    async def on_panel_cb(_: Client, q: CallbackQuery) -> None:
        if not await _admin_only(q):
            return
        section = q.matches[0].group(1)
        if section == "home":
            text = "**🛠 Admin panel**\n\nPick a section:"
            kb = _panel_home_kb()
        elif section == "pending":
            text, kb = await _render_pending()
        elif section == "approved":
            text, kb = await _render_approved()
        else:
            text, kb = await _render_banned()
        try:
            await q.message.edit_text(text, reply_markup=kb)
        except Exception:  # noqa: BLE001
            pass
        await q.answer()
