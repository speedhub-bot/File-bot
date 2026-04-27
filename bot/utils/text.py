from __future__ import annotations

from pathlib import Path

# Extensions we treat as "text" -> split on line/word boundaries.
TEXT_EXTS: frozenset[str] = frozenset(
    {
        ".txt", ".md", ".markdown", ".rst",
        ".csv", ".tsv", ".json", ".jsonl", ".ndjson",
        ".yaml", ".yml", ".xml", ".html", ".htm",
        ".log", ".ini", ".toml", ".conf", ".cfg", ".env",
        ".py", ".pyi", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs",
        ".java", ".kt", ".scala",
        ".c", ".h", ".cpp", ".cc", ".hpp", ".hh", ".hxx",
        ".rs", ".go", ".rb", ".php", ".pl", ".sh", ".bash", ".zsh",
        ".sql", ".css", ".scss", ".sass", ".less",
        ".vue", ".svelte", ".tex", ".bib",
        ".srt", ".vtt", ".ass",
    }
)


def looks_like_text(path: Path) -> bool:
    """First check extension; otherwise sniff first 8 KB for binary bytes."""

    if path.suffix.lower() in TEXT_EXTS:
        return True
    try:
        with path.open("rb") as fh:
            sample = fh.read(8192)
    except OSError:
        return False
    if not sample:
        return False
    if b"\x00" in sample:
        return False
    # Heuristic: if >30% are non-printable / non-whitespace, treat as binary.
    text_chars = bytes({7, 8, 9, 10, 11, 12, 13, 27} | set(range(0x20, 0x100)) - {0x7F})
    nontext = sum(1 for b in sample if b not in text_chars)
    return (nontext / len(sample)) <= 0.30


def find_split_offset(buf: bytes, target: int, slack: int) -> int:
    """Within [target - slack, target + slack], find the rightmost newline (or
    fallback to whitespace) byte and return the offset *just after* it. Falls
    back to ``target`` if nothing nice is found."""

    if target >= len(buf):
        return len(buf)

    lo = max(0, target - slack)
    hi = min(len(buf), target + slack)

    # 1. nearest newline at or before target
    nl = buf.rfind(b"\n", lo, target + 1)
    if nl != -1:
        return nl + 1
    # 2. nearest newline anywhere in window
    nl = buf.rfind(b"\n", lo, hi)
    if nl != -1:
        return nl + 1
    # 3. nearest whitespace at or before target
    for ws in (b" ", b"\t", b","):
        idx = buf.rfind(ws, lo, target + 1)
        if idx != -1:
            return idx + 1
    # 4. nothing nice found
    return target
