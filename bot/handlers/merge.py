from __future__ import annotations

import logging
import re
import shutil
import time
import uuid

from pyrogram import Client, filters
from pyrogram.types import Message

from bot.config import settings
from bot.db.repo import get_or_create_user
from bot.handlers.files import _safe_filename
from bot.services.quota import QuotaError, assert_can_accept
from bot.utils.format import bytes_human, progress_bar

log = logging.getLogger(__name__)

# user_id -> {dir: Path, parts: dict[index, path], output_name: str|None,
#             started_at: float, total_size: int}
SESSIONS: dict[int, dict] = {}

# Match files like "movie.part-03.mkv" / "movie.part-3-of-12.mkv" — we accept
# both the new-style (no "of-N") and any legacy-style names users might still
# have lying around.
_PART_RE = re.compile(r"\.part-(\d+)(?:-of-\d+)?(?:\.[^.]+)?$", re.IGNORECASE)


def _extract_index(name: str) -> int | None:
    m = _PART_RE.search(name)
    if not m:
        return None
    return int(m.group(1))


def _ensure_session(user_id: int) -> dict:
    sess = SESSIONS.get(user_id)
    if sess is None:
        d = settings.work_dir / f"merge-{user_id}-{uuid.uuid4().hex[:8]}"
        d.mkdir(parents=True, exist_ok=True)
        sess = {
            "dir": d,
            "parts": {},
            "sizes": {},  # idx -> last-recorded size, used to undo the
                          # previous contribution to total_size when a user
                          # re-sends a part with the same index.
            "output_name": None,
            "started_at": time.time(),
            "total_size": 0,
            "gap_warned": False,
        }
        SESSIONS[user_id] = sess
    return sess


def _drop_session(user_id: int) -> None:
    sess = SESSIONS.pop(user_id, None)
    if sess and sess["dir"].exists():
        shutil.rmtree(sess["dir"], ignore_errors=True)


def register(app: Client) -> None:
    @app.on_message(filters.command(["merge"]))
    async def on_merge(client: Client, m: Message) -> None:
        u = m.from_user
        if not u:
            return
        user = await get_or_create_user(u.id, u.username, u.first_name)
        if user.is_banned:
            return

        args = (m.text or "").split(maxsplit=1)
        if len(args) >= 2:
            sub = args[1].strip().lower()
        else:
            sub = "help"

        if sub == "start":
            _drop_session(u.id)
            sess = _ensure_session(u.id)
            await m.reply_text(
                "🧷 *Merge mode armed.*\n\n"
                "Forward me the parts (`*.part-NN.ext`) one by one. I'll figure "
                "out the order from filenames. When you're done, send "
                "`/merge done <output-filename>` and I'll join + send it back.\n\n"
                "Working dir: `" + str(sess["dir"]) + "`\n"
                "/merge cancel to abort."
            )
            return

        if sub == "cancel":
            _drop_session(u.id)
            await m.reply_text("Merge session cancelled.")
            return

        if sub == "status":
            sess = SESSIONS.get(u.id)
            if not sess:
                await m.reply_text("No active merge session — `/merge start` to begin.")
                return
            indices = sorted(sess["parts"].keys())
            await m.reply_text(
                "*Merge session*\n"
                f"Parts received: `{len(indices)}`\n"
                f"Indices: `{indices}`\n"
                f"Total bytes: `{bytes_human(sess['total_size'])}`"
            )
            return

        if sub.startswith("done"):
            sess = SESSIONS.get(u.id)
            if not sess or not sess["parts"]:
                await m.reply_text("No parts collected — `/merge start` first.")
                return

            tail = (args[1].split(maxsplit=1)[1] if " " in args[1] else "").strip()
            output_name = _safe_filename(tail, f"merged-{u.id}-{int(time.time())}.bin")

            # Quota check using cumulative size.
            try:
                await assert_can_accept(u.id, sess["total_size"])
            except QuotaError as e:
                await m.reply_text(f"❌ {e}")
                return

            indices = sorted(sess["parts"].keys())
            # Warn once if there are gaps in the sequence; on the second
            # /merge done call we proceed with whatever the user has — that's
            # the contract the warning message promises.
            gaps = [i for i in range(indices[0], indices[-1] + 1) if i not in sess["parts"]]
            if gaps and not sess.get("gap_warned"):
                sess["gap_warned"] = True
                await m.reply_text(
                    f"⚠️ Missing parts: `{gaps}`. Send them and retry, or "
                    "`/merge done` again to merge what you have."
                )
                return

            target = sess["dir"] / output_name
            progress = await m.reply_text("🪡 *Merging…*")
            written = 0
            try:
                with target.open("wb") as out:
                    for idx in indices:
                        with sess["parts"][idx].open("rb") as inp:
                            while True:
                                chunk = inp.read(1024 * 1024)
                                if not chunk:
                                    break
                                out.write(chunk)
                                written += len(chunk)
                        try:
                            await client.edit_message_text(
                                m.chat.id, progress.id,
                                f"🪡 *Merging*\n`{progress_bar(written, sess['total_size'])}`\n"
                                f"{bytes_human(written)} / {bytes_human(sess['total_size'])}"
                            )
                        except Exception:  # noqa: BLE001
                            pass
                await client.edit_message_text(m.chat.id, progress.id, "📤 *Uploading merged file…*")
                await client.send_document(
                    m.chat.id,
                    document=str(target),
                    caption=f"`{output_name}` ({bytes_human(written)})",
                )
                await client.edit_message_text(m.chat.id, progress.id, "✅ *Done.*")
            finally:
                _drop_session(u.id)
            return

        # Default — show help.
        await m.reply_text(
            "*🧷 Merge command*\n"
            "`/merge start` — begin collecting parts\n"
            "`/merge status` — show what's been collected\n"
            "`/merge done <output-name>` — join everything and send it back\n"
            "`/merge cancel` — abort the session"
        )

    async def _is_merging(_flt, _client, m: Message) -> bool:
        u = m.from_user
        if not u or u.id not in SESSIONS:
            return False
        if not m.document or not m.document.file_name:
            return False
        return _extract_index(m.document.file_name) is not None

    merging_part = filters.create(_is_merging)

    # group=-1 runs before the default file handler, so a part file forwarded
    # during an active merge session is *consumed* here instead of triggering
    # the normal split-mode prompt.
    @app.on_message(merging_part & filters.private, group=-1)
    async def collect_part(client: Client, m: Message) -> None:
        u = m.from_user
        sess = SESSIONS[u.id]
        idx = _extract_index(m.document.file_name)
        size = m.document.file_size or 0
        target = sess["dir"] / _safe_filename(
            m.document.file_name, f"part-{idx}.bin"
        )
        prev_path = sess["parts"].get(idx)
        prev_size = sess["sizes"].get(idx, 0)

        notice = await m.reply_text(f"⬇️  Receiving part #{idx} ({bytes_human(size)})…")
        try:
            await client.download_media(message=m, file_name=str(target))
        except Exception as e:  # noqa: BLE001
            # If the new download was about to overwrite the same path used by
            # the previous part for this index, that file may now be partially
            # clobbered. Drop the entry so /merge done can't try to read a
            # corrupt part. For different-path failures the previous entry is
            # still good — leave the session untouched.
            if prev_path is not None and prev_path == target:
                sess["parts"].pop(idx, None)
                sess["sizes"].pop(idx, None)
                sess["total_size"] -= prev_size
            await notice.edit_text(f"❌ Failed: `{e}`")
            return

        # Download succeeded — apply the rollback + bookkeeping atomically.
        if prev_path is not None:
            sess["total_size"] -= prev_size
            # Same-path resends were just overwritten by download_media, so
            # we must NOT unlink. Different-path resends leave an orphan that
            # we clean up here.
            if prev_path != target:
                try:
                    prev_path.unlink(missing_ok=True)
                except OSError:
                    pass
        sess["parts"][idx] = target
        sess["sizes"][idx] = size
        sess["total_size"] += size
        await notice.edit_text(
            f"✅ Got part #{idx}. Total received: `{len(sess['parts'])}` "
            f"({bytes_human(sess['total_size'])}). Send `/merge done <name>` "
            "when ready."
        )
        m.stop_propagation()
