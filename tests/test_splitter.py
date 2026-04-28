"""Offline unit tests for the smart splitter — no Telegram or DB needed."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

# Allow running this without the project's full env (BOT_TOKEN etc).
os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "x")
os.environ.setdefault("ADMIN_ID", "1")

from bot.handlers.files import _safe_filename  # noqa: E402
from bot.services.splitter import part_filename, part_size_for_count, split_file  # noqa: E402
from bot.utils.format import parse_count, parse_size  # noqa: E402
from bot.utils.text import find_split_offset, looks_like_text  # noqa: E402


def test_parse_size_ok() -> None:
    assert parse_size("100 mb") == 100 * 1024**2
    assert parse_size("1.5gb") == int(1.5 * 1024**3)
    assert parse_size("750k") == 750 * 1024
    assert parse_size("4096") == 4096


def test_parse_size_bad() -> None:
    with pytest.raises(ValueError):
        parse_size("not a size")


def test_parse_count_ok() -> None:
    assert parse_count("20") == 20
    assert parse_count("20 parts") == 20


def test_part_size_for_count() -> None:
    assert part_size_for_count(1000, 4) == 250
    assert part_size_for_count(1001, 4) == 251
    assert part_size_for_count(1000, 1) == 1000


def test_part_filename_no_total_in_name() -> None:
    # Should NOT embed "of-N" — only a zero-padded index.
    name = part_filename("movie.mkv", 3, 12)
    assert name == "movie.part-03.mkv"
    # No suffix case
    assert part_filename("README", 1, 5) == "README.part-01"
    # Index padding widens with larger estimated totals
    assert part_filename("a.bin", 7, 100) == "a.part-007.bin"


def test_safe_filename_strips_path_traversal() -> None:
    assert _safe_filename("../../etc/passwd", "fb") == "passwd"
    assert _safe_filename("/etc/shadow", "fb") == "shadow"
    assert _safe_filename("..\\..\\evil.bat", "fb") == "evil.bat"
    assert _safe_filename("normal.txt", "fb") == "normal.txt"
    # Pure path components → fallback
    assert _safe_filename("..", "fb") == "fb"
    assert _safe_filename("", "fb") == "fb"
    assert _safe_filename(None, "fb") == "fb"
    # Embedded NULs / control chars stripped
    assert _safe_filename("foo\x00.txt", "fb") == "foo_.txt"




def test_is_privileged_admin_and_vip(monkeypatch) -> None:
    """Admin and VIPs must be marked privileged so they bypass the
    single-user queue and the per-user daily quota."""
    from bot.config import settings
    from bot.services.quota import _is_privileged

    class _U:
        def __init__(self, uid, vip=False):
            self.user_id = uid
            self.is_vip = vip

    monkeypatch.setattr(settings, "admin_id", 999)
    assert _is_privileged(None) is False
    assert _is_privileged(_U(1)) is False
    assert _is_privileged(_U(1, vip=True)) is True
    assert _is_privileged(_U(999)) is True  # admin


def test_jobmanager_user_busy_starts_false() -> None:
    """The single-user lock starts un-acquired — first non-VIP job must
    not be told they're queued."""
    import asyncio

    from bot.services.jobs import JobManager

    async def _check():
        jm = JobManager()
        assert jm.user_busy is False
        async with jm._user_lock:
            assert jm.user_busy is True
        assert jm.user_busy is False

    asyncio.run(_check())




def test_quota_blocks_banned_users() -> None:
    """assert_can_accept must raise QuotaError for banned users — defense in
    depth in case the handler-level ban check is bypassed."""

    import tempfile
    import time

    from bot.config import settings as _settings
    from bot.db.db import User, _SessionMaker, init_db
    from bot.services.quota import QuotaError, assert_can_accept

    # Unique per-run id so reruns against the same SQLite file don't
    # collide on the UNIQUE constraint.
    uid = int(time.time() * 1000) % (2**31)

    async def _run() -> None:
        # Use a temporary work_dir so disk_used_bytes isn't influenced by
        # whatever else is on the box.
        with tempfile.TemporaryDirectory() as d:
            _settings.work_dir = Path(d)
            await init_db()
            async with _SessionMaker() as s:
                u = User(user_id=uid, username="b", first_name="b", is_banned=True)
                s.add(u)
                await s.commit()
            try:
                await assert_can_accept(uid, 1024)
            except QuotaError as e:
                assert "banned" in str(e).lower()
                return
            raise AssertionError("expected QuotaError for banned user")

    asyncio.run(_run())


def test_find_split_offset_prefers_newline() -> None:
    buf = b"line1\nline2\nline3\nline4\n"
    cut = find_split_offset(buf, target=10, slack=4)
    # Should land on a position right after a newline.
    assert buf[cut - 1 : cut] == b"\n"


def test_find_split_offset_falls_back_to_space() -> None:
    buf = b"word " * 50
    cut = find_split_offset(buf, target=100, slack=10)
    # Must land just after a space.
    assert buf[cut - 1 : cut] == b" "


def test_looks_like_text_ext() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.py"
        p.write_text("print('hi')\n")
        assert looks_like_text(p)


def test_looks_like_text_binary() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.bin"
        p.write_bytes(b"\x00\x01\x02" * 1024)
        assert not looks_like_text(p)


def test_byte_split_roundtrip() -> None:
    async def _run() -> None:
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "blob.bin"
            payload = os.urandom(50_000)
            src.write_bytes(payload)
            out = Path(d) / "parts"
            collected: list[Path] = []
            async for part in split_file(src, out, part_size=10_000):
                collected.append(part.path)
            assert len(collected) == 5
            recombined = b"".join(p.read_bytes() for p in collected)
            assert recombined == payload

    asyncio.run(_run())


def test_text_split_aligns_on_lines() -> None:
    async def _run() -> None:
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "lines.txt"
            text = ("line %05d aaaaaaaaaaaaa\n" % i for i in range(2000))
            content = "".join(text).encode()
            src.write_bytes(content)
            out = Path(d) / "parts"
            collected: list[Path] = []
            async for part in split_file(src, out, part_size=8_000):
                collected.append(part.path)
            assert len(collected) >= 2
            # No part starts mid-line (each part begins right after a newline
            # boundary, except the very first one).
            for i, p in enumerate(collected):
                data = p.read_bytes()
                assert data, f"part {i} empty"
                if i > 0:
                    assert data[:1] == b"l", f"part {i} starts mid-line"
                # No trailing partial line either.
                assert data.endswith(b"\n")
            # Round-trip
            recombined = b"".join(p.read_bytes() for p in collected)
            assert recombined == content

    asyncio.run(_run())


def test_credit_line_uses_pyrogram_markdown_dialect() -> None:
    """Pyrogram 2's MARKDOWN parser uses doubled markers (`**bold**`,
    `__italic__`); single markers are sent literally. Catch any regression
    where someone re-introduces single-marker formatting in CREDIT_LINE
    (which is rendered into every /start, /help and /profile message)."""
    from bot.handlers.start import CREDIT_LINE

    assert "@akaza_isnt" in CREDIT_LINE, "credit handle must be @akaza_isnt"
    # No backslash escapes leaking into the rendered message.
    assert "\\_" not in CREDIT_LINE
    # No single-marker italics. `__Bot by__` is fine; `_Bot by_` would render literal.
    import re

    # Strip the `[label](url)` link form first so '_' inside the URL doesn't trip us.
    body = re.sub(r"\[[^\]]*\]\([^)]*\)", "", CREDIT_LINE)
    assert not re.search(r"(?<!_)_[^_\s][^_]*[^_\s]_(?!_)", body), (
        "single-underscore italic markers won't render in Pyrogram 2 markdown"
    )


def test_handler_messages_have_no_unmatched_single_markers() -> None:
    """Walk every static message string in the handlers and assert no
    Telegram-Bot-API-style single-marker bold/italic snuck back in. Would
    have caught the v3 bug where messages rendered with literal '*' / '_'."""
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    files = [
        repo / "bot" / "handlers" / "start.py",
        repo / "bot" / "handlers" / "admin.py",
        repo / "bot" / "handlers" / "files.py",
        repo / "bot" / "handlers" / "splits.py",
        repo / "bot" / "handlers" / "cookies.py",
        repo / "bot" / "services" / "jobs.py",
    ]
    # Match `*X*` where X has no asterisks, the marker isn't doubled, and
    # the asterisks aren't inside a backtick code span (those are literal).
    bold = re.compile(r"(?<!\*)(?<!`)\*(?!\*)(?!\s)[^*\n`]+?(?<!\s)\*(?!\*)(?!`)")
    for f in files:
        for ln, line in enumerate(f.read_text().splitlines(), 1):
            stripped = line.lstrip()
            if not stripped.startswith(('"', "'", "f\"", "f'")):
                continue
            if bold.search(line):
                raise AssertionError(
                    f"{f.name}:{ln} contains a single-marker bold "
                    f"that won't render: {line.rstrip()!r}"
                )


def test_quota_accepts_jobs_with_unlimited_disk_budget(monkeypatch) -> None:
    """`disk_budget_bytes == 0` must mean 'no explicit cap, fall back to
    actual free space minus 512 MB slack'. The previous version always
    rejected every job because `min(0, free_left)` was 0.
    """
    import asyncio
    import tempfile
    import time

    from bot.config import settings as _settings
    from bot.db.db import User, _SessionMaker, init_db
    from bot.services import quota as _quota
    from bot.services.quota import assert_can_accept

    monkeypatch.setattr(_settings, "disk_budget_bytes", 0)
    monkeypatch.setattr(_settings, "per_user_daily_bytes", 0)
    # Pretend the volume has 100 GB free so we definitely have headroom.
    monkeypatch.setattr(_quota, "free_disk_bytes", lambda: 100 * 1024**3)
    monkeypatch.setattr(_quota, "disk_used_bytes", lambda: 0)

    uid = int(time.time() * 1000) % (2**31) + 1

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as d:
            _settings.work_dir = Path(d)
            await init_db()
            async with _SessionMaker() as s:
                s.add(User(user_id=uid, username="ok", first_name="ok"))
                await s.commit()
            # 1 GB file should be fine on a 100 GB free volume.
            await assert_can_accept(uid, 1024 * 1024 * 1024)

    asyncio.run(_run())


def test_health_ready_ok_with_unlimited_disk_budget(monkeypatch) -> None:
    """`/ready` must return 200 when `disk_budget_bytes == 0` and the
    actual filesystem has space, not 503 forever (which kept Railway/Fly
    in a healthcheck-restart loop)."""
    import asyncio

    from aiohttp.test_utils import make_mocked_request

    from bot.config import settings as _settings
    from bot.services import health as _health

    monkeypatch.setattr(_settings, "disk_budget_bytes", 0)
    monkeypatch.setattr(_health, "disk_used_bytes", lambda: 1024)
    monkeypatch.setattr(_health, "free_disk_bytes", lambda: 10 * 1024**3)

    async def _run() -> None:
        req = make_mocked_request("GET", "/ready")
        resp = await _health._ready(req)
        assert resp.status == 200, f"expected 200 OK, got {resp.status}"

    asyncio.run(_run())


def test_access_request_lifecycle(monkeypatch) -> None:
    """End-to-end on the repo layer: record_access_request → list_pending →
    set_vip+clear_request → list_approved → set_banned → list_banned."""
    import asyncio
    import tempfile
    import time

    from bot.config import settings as _settings
    from bot.db.db import User, _SessionMaker, init_db
    from bot.db.repo import (
        clear_request,
        list_approved,
        list_banned,
        list_pending_requests,
        record_access_request,
        set_banned,
        set_vip,
    )

    uid = int(time.time() * 1000) % (2**31) + 100

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as d:
            _settings.work_dir = Path(d)
            await init_db()
            async with _SessionMaker() as s:
                s.add(User(user_id=uid, username="req", first_name="Requester"))
                await s.commit()

            assert await record_access_request(uid) is True
            # second call is a no-op (already pending).
            assert await record_access_request(uid) is False

            pending = await list_pending_requests()
            assert any(p.user_id == uid for p in pending)

            await set_vip(uid, True)
            await clear_request(uid)
            approved = await list_approved()
            assert any(p.user_id == uid for p in approved)
            pending2 = await list_pending_requests()
            assert all(p.user_id != uid for p in pending2)

            # Approved → banned demotes from VIP via the panel flow.
            await set_vip(uid, False)
            await set_banned(uid, True)
            banned = await list_banned()
            assert any(p.user_id == uid for p in banned)

            # Banned users cannot re-request silently.
            assert await record_access_request(uid) is False

    asyncio.run(_run())
