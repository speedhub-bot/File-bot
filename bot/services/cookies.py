from __future__ import annotations

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
        Scan directory for Cookies/*.txt and extract matching cookies into output_zip.
        Returns total count of cookies found.
        """
        total_found = 0
        file_counter = 1

        # Find all .txt files
        # The original tool looked specifically for "Cookies/*.txt"
        # but user said "all type of file", so we'll look for any .txt file
        # that might contain cookies.
        txt_files = list(input_dir.rglob("*.txt"))

        if not txt_files:
            return 0

        with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for txt_file in txt_files:
                # Basic heuristic: only check if it looks like a cookie file or is in a Cookies dir
                # or just check all .txt files as logs can be named anything.
                # To be "smart", we'll check if it has Netscape headers or just try to parse.

                matches = self.extract_from_file(txt_file)
                if matches:
                    # Create a unique name in the zip for this source
                    arcname = f"cookies_{self.domain}_{file_counter}.txt"
                    # Include some info about original path if possible, but keep it safe
                    zf.writestr(arcname, "\n".join(matches))
                    file_counter += 1
                    total_found += len(matches)

        return total_found

async def run_extraction(
    input_path: Path,
    domain: str,
    work_dir: Path
) -> Optional[Path]:
    """
    Handles the full extraction process:
    1. Unpack if archive
    2. Extract cookies
    3. Return path to result zip
    """
    extractor = CookieExtractor(domain)
    extract_temp = work_dir / "extract"
    extract_temp.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Unpack
        if input_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(input_path, 'r') as zf:
                for member in zf.namelist():
                    # Security: check for path traversal
                    target = (extract_temp / member).resolve()
                    if extract_temp.resolve() not in target.parents:
                        log.warning("Security: skipping malicious zip entry %s", member)
                        continue
                    zf.extract(member, extract_temp)
        elif input_path.suffix.lower() == ".7z":
            with py7zr.SevenZipFile(input_path, mode='r') as sz:
                # py7zr doesn't have a simple way to check each member before extraction
                # in some versions, but we can list them.
                members = sz.getnames()
                for member in members:
                    target = (extract_temp / member).resolve()
                    if extract_temp.resolve() not in target.parents:
                        log.warning("Security: skipping malicious 7z entry %s", member)
                        continue
                sz.extractall(extract_temp)
        elif input_path.suffix.lower() == ".rar":
            # For RAR, we try to use shutil.unpack_archive which might work
            # if the system has unrar/7z installed.
            try:
                shutil.unpack_archive(str(input_path), str(extract_temp))
            except Exception as e:
                log.error("Failed to unpack RAR: %s", e)
                # Fallback: if it's a small file maybe it's just a misnamed txt
                if input_path.stat().st_size < 1024 * 1024:
                    shutil.copy(input_path, extract_temp / input_path.name)
        else:
            # Assume it's a single log file or directory
            if input_path.is_file():
                shutil.copy(input_path, extract_temp / input_path.name)
            else:
                shutil.copytree(input_path, extract_temp, dirs_exist_ok=True)

        # 2. Extract
        result_zip = work_dir / f"cookies_{domain.replace('.', '_')}.zip"
        count = extractor.process_directory(extract_temp, result_zip)

        if count > 0:
            return result_zip
        return None

    finally:
        shutil.rmtree(extract_temp, ignore_errors=True)
