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


def test_merge_extract_index() -> None:
    from bot.handlers.merge import _extract_index

    assert _extract_index("movie.part-03.mkv") == 3
    assert _extract_index("movie.part-007.bin") == 7
    # Legacy names from earlier versions.
    assert _extract_index("movie.part-03-of-12.mkv") == 3
    # Files without the part suffix shouldn't match.
    assert _extract_index("notes.txt") is None
    assert _extract_index("backup.tar.gz") is None


def _simulate_collect(sess: dict, idx: int, filename: str, size: int,
                      *, fail: bool = False) -> None:
    """Mirror of bot/handlers/merge.py::collect_part dedup + bookkeeping
    so we can exercise both success and failure paths in unit tests."""
    target = sess["dir"] / filename
    prev_path = sess["parts"].get(idx)
    prev_size = sess["sizes"].get(idx, 0)

    if fail:
        # Simulate a partial download corrupting the same-path file.
        if prev_path == target and target.exists():
            target.write_bytes(b"\x00" * 1)  # corrupted partial
        if prev_path is not None and prev_path == target:
            sess["parts"].pop(idx, None)
            sess["sizes"].pop(idx, None)
            sess["total_size"] -= prev_size
        return

    # Successful download: write the new bytes (overwriting if same path).
    target.write_bytes(b"x" * size)

    if prev_path is not None:
        sess["total_size"] -= prev_size
        if prev_path != target:
            prev_path.unlink(missing_ok=True)
    sess["parts"][idx] = target
    sess["sizes"][idx] = size
    sess["total_size"] += size


def test_merge_dedup_same_filename_does_not_self_destruct(tmp_path) -> None:
    sess = {"parts": {}, "sizes": {}, "total_size": 0, "dir": tmp_path}
    idx = 3

    _simulate_collect(sess, idx, "movie.part-03.mkv", 100)
    assert sess["total_size"] == 100
    assert sess["parts"][idx].stat().st_size == 100

    # Re-send same filename: file must survive the dedup and total_size must
    # reflect only the new size, not new + old.
    _simulate_collect(sess, idx, "movie.part-03.mkv", 250)
    assert sess["total_size"] == 250
    assert sess["parts"][idx].stat().st_size == 250

    # Re-send different filename for same idx: orphan must be removed.
    _simulate_collect(sess, idx, "movie.part-3.mkv", 80)
    assert sess["total_size"] == 80
    assert sess["parts"][idx].name == "movie.part-3.mkv"
    assert not (tmp_path / "movie.part-03.mkv").exists()


def test_merge_failed_resend_does_not_corrupt_session(tmp_path) -> None:
    """If download_media raises while replacing a part:
       * same-path failure → entry must be dropped (file is partially clobbered)
       * different-path failure → entry must be unchanged (old part still usable)
    """
    sess = {"parts": {}, "sizes": {}, "total_size": 0, "dir": tmp_path}
    idx = 3

    _simulate_collect(sess, idx, "movie.part-03.mkv", 100)
    snapshot = (sess["parts"][idx], sess["sizes"][idx], sess["total_size"])

    # Different-path failure → session untouched.
    _simulate_collect(sess, idx, "movie.part-3.mkv", 999, fail=True)
    assert (sess["parts"][idx], sess["sizes"][idx], sess["total_size"]) == snapshot, \
        "different-path failure must not mutate session state"

    # Same-path failure → entry must be dropped, total_size rolled back.
    _simulate_collect(sess, idx, "movie.part-03.mkv", 999, fail=True)
    assert idx not in sess["parts"], "same-path failure must drop the entry"
    assert idx not in sess["sizes"]
    assert sess["total_size"] == 0


def test_merge_gap_warned_only_blocks_first_call() -> None:
    """First /merge done with gaps warns; the second call must proceed
    rather than re-blocking the user."""
    sess = {"parts": {1: "a", 3: "c"}, "gap_warned": False}
    indices = sorted(sess["parts"].keys())
    gaps = [i for i in range(indices[0], indices[-1] + 1) if i not in sess["parts"]]
    assert gaps == [2]
    blocked_first = bool(gaps) and not sess.get("gap_warned")
    if blocked_first:
        sess["gap_warned"] = True
    assert blocked_first is True
    blocked_second = bool(gaps) and not sess.get("gap_warned")
    assert blocked_second is False


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


def test_url_filename_extraction() -> None:
    from bot.handlers.url import _filename_from_url

    assert _filename_from_url("https://example.com/path/file.zip", 1) == "file.zip"
    # URL-encoded spaces should round-trip and be sanitized.
    assert _filename_from_url("https://example.com/My%20Doc.pdf", 1) == "My Doc.pdf"
    # No path → fallback.
    assert _filename_from_url("https://example.com", 42) == "download-42.bin"
    # Path traversal in URL paths still gets stripped.
    assert _filename_from_url("https://x/../../etc/passwd", 1) == "passwd"


def test_quota_blocks_banned_users() -> None:
    """assert_can_accept must raise QuotaError for banned users — defense in
    depth in case the handler-level ban check is bypassed."""

    import tempfile

    from bot.config import settings as _settings
    from bot.db.db import User, _SessionMaker, init_db
    from bot.services.quota import QuotaError, assert_can_accept

    async def _run() -> None:
        # Use a temporary work_dir so disk_used_bytes isn't influenced by
        # whatever else is on the box.
        with tempfile.TemporaryDirectory() as d:
            _settings.work_dir = Path(d)
            await init_db()
            async with _SessionMaker() as s:
                u = User(user_id=99001, username="b", first_name="b", is_banned=True)
                s.add(u)
                await s.commit()
            try:
                await assert_can_accept(99001, 1024)
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
