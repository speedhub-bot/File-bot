from __future__ import annotations

import math
import re

import humanize

_UNITS = {
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
}


def bytes_human(n: int | float) -> str:
    return humanize.naturalsize(int(n), binary=True)


def parse_size(text: str) -> int:
    """Parse '100 mb', '1.5 GB', '750k', '1024' -> bytes. Raises ValueError."""

    s = text.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmgb]?b?)", s)
    if not m:
        raise ValueError(f"Could not parse size: {text!r}")
    qty = float(m.group(1))
    unit = m.group(2) or "b"
    if unit not in _UNITS:
        raise ValueError(f"Unknown unit: {unit!r}")
    return int(qty * _UNITS[unit])


def parse_count(text: str) -> int:
    """Parse '20', '20 parts' -> 20."""

    s = text.strip().lower().replace("parts", "").replace("part", "").strip()
    if not s.isdigit():
        raise ValueError(f"Could not parse count: {text!r}")
    n = int(s)
    if n <= 0:
        raise ValueError("Count must be > 0")
    return n


def progress_bar(done: int, total: int, width: int = 16) -> str:
    if total <= 0:
        return "[" + "?" * width + "]"
    pct = max(0.0, min(1.0, done / total))
    filled = int(round(pct * width))
    return "[" + "█" * filled + "░" * (width - filled) + f"] {pct * 100:5.1f}%"


def part_count_for_size(total: int, part_size: int) -> int:
    return max(1, math.ceil(total / max(1, part_size)))
