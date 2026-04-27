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
