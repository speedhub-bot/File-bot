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

from bot.services.splitter import part_size_for_count, split_file  # noqa: E402
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
