from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pyrogram import Client
from pyrogram.types import Message

from bot.config import settings
from bot.db.repo import (
    add_quota_used,
    get_user,
    insert_job,
    update_job,
)
from bot.services.quota import QuotaError, _is_privileged, assert_can_accept
from bot.services.splitter import part_size_for_count, split_file
from bot.utils.format import bytes_human, progress_bar

log = logging.getLogger(__name__)

# Telegram client edits are rate-limited; refuse to edit more than once a second.
MIN_EDIT_INTERVAL = 1.0


@dataclass
class JobRequest:
    user_id: int
    chat_id: int
    src_message_id: int
    file_name: str
    file_size: int
    mode: str  # "none" | "auto" | "size" | "count"
    value: int = 0  # bytes for "size", N for "count", ignored otherwise
    progress_msg_id: int = 0
    job_db_id: int = 0
    work_dir: Path = field(default_factory=lambda: settings.work_dir)


class _NullCM:
    """Async no-op context manager used when a job should bypass the
    single-user queue (admin / VIP)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class JobManager:
    """Single-process FIFO job runner with two layers of gating:

    * ``_user_lock`` (Semaphore(1)) — only one *non-privileged* job may run
      at a time. Other users get a "you've been queued" message and wait
      their turn. Admin and VIPs bypass this lock entirely.
    * ``_sem`` (Semaphore(max_concurrent_jobs)) — secondary cap that
      includes admin/VIP traffic, so even unprivileged + admin combined
      can't hammer the disk.
    """

    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._user_lock = asyncio.Semaphore(1)
        self._active: dict[int, JobRequest] = {}
        self._lock = asyncio.Lock()

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def active_jobs(self) -> list[JobRequest]:
        return list(self._active.values())

    @property
    def user_busy(self) -> bool:
        """True iff the single-user lock is currently held by someone."""
        return self._user_lock.locked()

    async def submit(self, client: Client, req: JobRequest) -> None:
        try:
            await assert_can_accept(req.user_id, req.file_size)
        except QuotaError as e:
            await client.send_message(req.chat_id, f"❌ {e}")
            return

        user = await get_user(req.user_id)
        privileged = _is_privileged(user)

        # The DB row for this job — useful for /jobs even when concurrency-capped.
        req.job_db_id = await insert_job(req.user_id, req.file_name, req.file_size)

        # If a non-privileged user lands here while another non-privileged
        # job is already running, surface a clear "queued" message instead
        # of letting them stare at silence. Admin/VIP skip the lock entirely.
        if not privileged and self.user_busy:
            await client.send_message(
                req.chat_id,
                "⏳ *Bot is busy with another user.*\n"
                "I've queued your job — you'll start automatically when the "
                "current one finishes. Send /cancel if you'd rather not wait.",
            )

        gate = self._user_lock if not privileged else _NullCM()
        async with gate:  # acquired immediately for admin/VIP
            async with self._sem:
                async with self._lock:
                    self._active[req.job_db_id] = req
                try:
                    await self._run(client, req)
                finally:
                    async with self._lock:
                        self._active.pop(req.job_db_id, None)

    async def _run(self, client: Client, req: JobRequest) -> None:
        await update_job(req.job_db_id, status="running", mode=req.mode)
        # Fresh per-job working directory keeps cleanup trivial.
        job_dir = req.work_dir / f"job-{req.job_db_id}-{uuid.uuid4().hex[:8]}"
        job_dir.mkdir(parents=True, exist_ok=True)
        downloaded: Path | None = None
        try:
            progress_msg = await client.send_message(
                req.chat_id,
                "📥 *Starting download…*",
            )
            req.progress_msg_id = progress_msg.id

            # ---------- 1. Download ----------
            downloaded = await self._download(client, req, job_dir)
            actual_size = downloaded.stat().st_size

            # ---------- 2. Decide split plan ----------
            part_size = self._resolve_part_size(actual_size, req)
            if part_size >= actual_size:
                # Single-shot — just re-upload.
                await self._edit_progress(client, req, "📤 *Uploading file…*")
                await client.send_document(
                    req.chat_id,
                    document=str(downloaded),
                    caption=f"`{downloaded.name}` ({bytes_human(actual_size)})",
                    progress=self._make_upload_progress(client, req, 1, 1),
                )
                await update_job(req.job_db_id, parts=1, status="done", finished=True)
            else:
                # ---------- 3. Split & upload one part at a time ----------
                idx = 0
                async for part in split_file(
                    downloaded,
                    job_dir / "parts",
                    part_size=part_size,
                    progress=self._make_split_progress(client, req, actual_size),
                ):
                    idx = part.index
                    await self._edit_progress(
                        client,
                        req,
                        f"📤 *Uploading part {part.index}…*",
                    )
                    await client.send_document(
                        req.chat_id,
                        document=str(part.path),
                        caption=f"`{part.path.name}` ({bytes_human(part.size)})",
                        progress=self._make_upload_progress(client, req, part.index, 0),
                    )
                    # Free disk before the next part is materialized.
                    try:
                        part.path.unlink()
                    except OSError:
                        pass
                await update_job(req.job_db_id, parts=idx, status="done", finished=True)

            await add_quota_used(req.user_id, actual_size)
            await self._edit_progress(client, req, "✅ *Done.*", final=True)

        except Exception as exc:  # noqa: BLE001
            log.exception("job %s failed", req.job_db_id)
            await update_job(req.job_db_id, status="failed", error=str(exc), finished=True)
            try:
                await client.send_message(req.chat_id, f"❌ Job failed: `{exc}`")
            except Exception:
                pass
        finally:
            # Always nuke the working directory.
            shutil.rmtree(job_dir, ignore_errors=True)

    # ---------------------------------------------------------------- helpers

    def _resolve_part_size(self, total: int, req: JobRequest) -> int:
        if req.mode == "none":
            return total
        if req.mode == "auto":
            return min(settings.default_auto_part_bytes, settings.max_part_bytes)
        if req.mode == "size":
            return min(max(1, req.value), settings.max_part_bytes)
        if req.mode == "count":
            return part_size_for_count(total, max(1, req.value))
        raise ValueError(f"unknown split mode {req.mode!r}")

    async def _download(self, client: Client, req: JobRequest, job_dir: Path) -> Path:
        # Defense in depth: even if the handler somehow lets a path-bearing
        # filename through, only its basename is ever used here.
        safe_name = Path(req.file_name).name or "file.bin"
        target = (job_dir / safe_name).resolve()
        if job_dir.resolve() not in target.parents:
            raise RuntimeError("Refusing to download outside the job directory.")
        # Pyrogram's progress_args lets us pass our own state.
        state = {"last": 0.0}
        total = max(1, req.file_size)

        async def _progress(current: int, _total: int) -> None:
            now = time.monotonic()
            if now - state["last"] < MIN_EDIT_INTERVAL and current < total:
                return
            state["last"] = now
            await self._edit_progress(
                client,
                req,
                f"📥 *Downloading*\n`{progress_bar(current, total)}`\n"
                f"{bytes_human(current)} / {bytes_human(total)}",
            )

        msg = await client.get_messages(req.chat_id, req.src_message_id)
        if not isinstance(msg, Message):
            raise RuntimeError("Source message no longer available")
        path = await client.download_media(
            message=msg,
            file_name=str(target),
            progress=_progress,
        )
        if path is None:
            raise RuntimeError("Telegram returned no file")
        return Path(path)

    def _make_split_progress(self, client: Client, req: JobRequest, total: int):
        state = {"last": 0.0}

        async def _p(current: int, _total: int) -> None:
            now = time.monotonic()
            if now - state["last"] < MIN_EDIT_INTERVAL and current < total:
                return
            state["last"] = now
            await self._edit_progress(
                client,
                req,
                f"✂️ *Splitting*\n`{progress_bar(current, total)}`\n"
                f"{bytes_human(current)} / {bytes_human(total)}",
            )

        return _p

    def _make_upload_progress(self, client: Client, req: JobRequest, idx: int, of: int):
        state = {"last": 0.0}
        suffix = f" {idx}/{of}" if of else f" #{idx}"

        async def _p(current: int, total: int) -> None:
            now = time.monotonic()
            if now - state["last"] < MIN_EDIT_INTERVAL and current < total:
                return
            state["last"] = now
            await self._edit_progress(
                client,
                req,
                f"📤 *Uploading part{suffix}*\n`{progress_bar(current, total)}`\n"
                f"{bytes_human(current)} / {bytes_human(total)}",
            )

        return _p

    async def _edit_progress(
        self, client: Client, req: JobRequest, text: str, *, final: bool = False
    ) -> None:
        if not req.progress_msg_id:
            return
        try:
            await client.edit_message_text(req.chat_id, req.progress_msg_id, text)
        except Exception as e:  # noqa: BLE001
            # Edits frequently fail with "MESSAGE_NOT_MODIFIED" — ignore silently.
            log.debug("edit_progress error: %s", e)
        if final:
            req.progress_msg_id = 0


jobs = JobManager()
