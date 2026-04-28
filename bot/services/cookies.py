from __future__ import annotations

import asyncio
import logging
import re
import shutil
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

import py7zr

log = logging.getLogger(__name__)

class CookieExtractor:
    """Extracts cookies from Netscape format files for a specific domain."""

    def __init__(self, domain: str):
        self.domain = domain.lower().lstrip(".")
        # Regex to match domain or subdomain
        # Matches domain.com, .domain.com, sub.domain.com
        self.domain_pattern = re.compile(
            rf"^(?:.*\.)?{re.escape(self.domain)}$", re.IGNORECASE
        )

    def _matches_domain(self, cookie_domain: str) -> bool:
        cookie_domain = cookie_domain.lower().lstrip(".")
        return (
            cookie_domain == self.domain
            or cookie_domain.endswith("." + self.domain)
        )

    def parse_cookie_line(self, line: str) -> Optional[Dict[str, str]]:
        parts = line.split("\t")
        if len(parts) < 7:
            return None
        try:
            return {
                "domain": parts[0].strip(),
                "flag": parts[1].strip(),
                "path": parts[2].strip(),
                "secure": parts[3].strip(),
                "expiration": parts[4].strip(),
                "name": parts[5].strip(),
                "value": parts[6].strip(),
            }
        except Exception:
            return None

    def extract_from_file(self, filepath: Path) -> List[str]:
        """Extract matching cookie lines from a single file."""
        results = []
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue

                    parts = line.split("\t")
                    if len(parts) >= 7:
                        cookie_domain = parts[0].strip().lower().lstrip(".")
                        if cookie_domain == self.domain or cookie_domain.endswith("." + self.domain):
                            results.append(line)
        except Exception as e:
            log.debug("Error reading %s: %s", filepath, e)
        return results

    def process_directory(self, input_dir: Path, output_zip: Path) -> int:
        """
        Scan directory for cookie-bearing files and extract matching cookies
        into *output_zip*.  Returns total count of cookies found.
        """
        total_found = 0
        file_counter = 1

        # Scan every common log/cookie extension – the original tool only
        # checked *.txt but real-world log dumps include many variants.
        _EXTENSIONS = (".txt", ".log", ".csv", ".tsv", ".cookie", ".cookies")
        candidate_files: list[Path] = []
        for f in input_dir.rglob("*"):
            if f.is_file() and f.suffix.lower() in _EXTENSIONS:
                candidate_files.append(f)

        if not candidate_files:
            return 0

        with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for cand_file in candidate_files:
                matches = self.extract_from_file(cand_file)
                if matches:
                    # Create a unique name in the zip for this source
                    arcname = f"cookies_{self.domain}_{file_counter}.txt"
                    # Include some info about original path if possible, but keep it safe
                    zf.writestr(arcname, "\n".join(matches))
                    file_counter += 1
                    total_found += len(matches)

        return total_found

def _sync_extraction(
    input_path: Path,
    domain: str,
    work_dir: Path,
) -> Optional[Path]:
    """CPU/IO-bound extraction — run via ``asyncio.to_thread``."""
    extractor = CookieExtractor(domain)
    extract_temp = work_dir / "extract"
    extract_temp.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Unpack
        suffix = input_path.suffix.lower()
        if suffix == ".zip":
            with zipfile.ZipFile(input_path, 'r') as zf:
                resolved_base = extract_temp.resolve()
                for member in zf.namelist():
                    target = (extract_temp / member).resolve()
                    if resolved_base not in target.parents and target != resolved_base:
                        log.warning("Security: skipping malicious zip entry %s", member)
                        continue
                    zf.extract(member, extract_temp)
        elif suffix == ".7z":
            with py7zr.SevenZipFile(input_path, mode='r') as sz:
                resolved_base = extract_temp.resolve()
                safe_members = []
                for member in sz.getnames():
                    target = (extract_temp / member).resolve()
                    if resolved_base not in target.parents and target != resolved_base:
                        log.warning("Security: skipping malicious 7z entry %s", member)
                        continue
                    safe_members.append(member)
                if safe_members:
                    sz.extract(extract_temp, targets=safe_members)
        elif suffix == ".rar":
            try:
                shutil.unpack_archive(str(input_path), str(extract_temp))
            except Exception as e:
                log.error("Failed to unpack RAR: %s", e)
                if input_path.stat().st_size < 1024 * 1024:
                    shutil.copy(input_path, extract_temp / input_path.name)
        else:
            if input_path.is_file():
                shutil.copy(input_path, extract_temp / input_path.name)
            else:
                shutil.copytree(input_path, extract_temp, dirs_exist_ok=True)

        # 2. Extract cookies
        result_zip = work_dir / f"cookies_{domain.replace('.', '_')}.zip"
        count = extractor.process_directory(extract_temp, result_zip)

        if count > 0:
            return result_zip
        return None

    finally:
        shutil.rmtree(extract_temp, ignore_errors=True)


async def run_extraction(
    input_path: Path,
    domain: str,
    work_dir: Path,
) -> Optional[Path]:
    """Offload the heavy I/O work to a thread so the event loop stays free."""
    return await asyncio.to_thread(_sync_extraction, input_path, domain, work_dir)
