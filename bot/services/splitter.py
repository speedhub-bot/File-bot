from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

from bot.utils.text import find_split_offset, looks_like_text

# Read buffer for the streaming splitter.
READ_BUFFER = 1024 * 1024  # 1 MiB

# When splitting on a text boundary, accept a window of ±5% around the target
# size to find a clean line/word break.
TEXT_BOUNDARY_SLACK = 0.05


@dataclass
class Part:
    index: int
    path: Path
    size: int


def part_filename(base: str, index: int, total: int) -> str:
    """`movie.mkv` + (3, 12) -> `movie.part-03-of-12.mkv`. For files without an
    extension we just append the suffix."""

    p = Path(base)
    stem = p.stem if p.suffix else p.name
    suffix = p.suffix
    width = max(2, len(str(total)))
    tag = f".part-{index:0{width}d}-of-{total:0{width}d}"
    return f"{stem}{tag}{suffix}"


async def _byte_split(
    src: Path, dst_dir: Path, target_size: int, total_parts: int,
    progress: Callable[[int, int], Awaitable[None]] | None,
) -> AsyncIterator[Part]:
    """Split ``src`` at exact byte offsets — safe for binary data."""

    written = 0
    bytes_emitted = 0
    total_size = src.stat().st_size
    idx = 0

    with src.open("rb") as fh:
        while True:
            idx += 1
            out_path = dst_dir / part_filename(src.name, idx, total_parts)
            remaining_in_part = target_size
            with out_path.open("wb") as out:
                while remaining_in_part > 0:
                    chunk = fh.read(min(READ_BUFFER, remaining_in_part))
                    if not chunk:
                        break
                    out.write(chunk)
                    remaining_in_part -= len(chunk)
                    written += len(chunk)
                    bytes_emitted += len(chunk)
                    if progress:
                        await progress(bytes_emitted, total_size)
            size = out_path.stat().st_size
            if size == 0:
                out_path.unlink(missing_ok=True)
                break
            yield Part(idx, out_path, size)
            if written >= total_size:
                break


async def _text_split(
    src: Path, dst_dir: Path, target_size: int, total_parts: int,
    progress: Callable[[int, int], Awaitable[None]] | None,
) -> AsyncIterator[Part]:
    """Split ``src`` aligning each cut to the nearest line / whitespace
    boundary. We never load more than ``target_size + slack`` into memory."""

    total_size = src.stat().st_size
    slack = max(1024, int(target_size * TEXT_BOUNDARY_SLACK))
    idx = 0
    bytes_emitted = 0

    with src.open("rb") as fh:
        carry = b""
        while True:
            idx += 1
            need = target_size + slack - len(carry)
            if need > 0:
                buf = carry + fh.read(need)
            else:
                buf = carry

            if not buf:
                break

            if len(buf) <= target_size + slack and fh.read(1) == b"":
                # Last chunk: write everything we have.
                out_path = dst_dir / part_filename(src.name, idx, total_parts)
                out_path.write_bytes(buf)
                bytes_emitted += len(buf)
                if progress:
                    await progress(bytes_emitted, total_size)
                yield Part(idx, out_path, len(buf))
                return

            # We over-read by 1 byte above when probing — put it back conceptually
            # by seeking back one byte.
            fh.seek(-1, 1)
            cut = find_split_offset(buf, target_size, slack)
            cut = max(1, min(cut, len(buf)))
            out_path = dst_dir / part_filename(src.name, idx, total_parts)
            out_path.write_bytes(buf[:cut])
            carry = buf[cut:]
            bytes_emitted += cut
            if progress:
                await progress(bytes_emitted, total_size)
            yield Part(idx, out_path, cut)


async def split_file(
    src: Path,
    dst_dir: Path,
    *,
    part_size: int,
    progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> AsyncIterator[Part]:
    """Yield :class:`Part` objects one at a time. The caller is expected to
    upload + delete each part before consuming the next one — this is what
    keeps disk usage flat."""

    dst_dir.mkdir(parents=True, exist_ok=True)
    total_size = src.stat().st_size
    total_parts = max(1, math.ceil(total_size / max(1, part_size)))
    is_text = looks_like_text(src)

    impl = _text_split if is_text else _byte_split
    async for part in impl(src, dst_dir, part_size, total_parts, progress):
        yield part
        # yield to event loop so progress edits / uploads can interleave
        await asyncio.sleep(0)


def part_size_for_count(total_size: int, count: int) -> int:
    if count <= 0:
        raise ValueError("count must be > 0")
    return max(1, math.ceil(total_size / count))
