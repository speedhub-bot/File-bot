import pytest
from pathlib import Path
from bot.services.cookies import CookieExtractor

def test_cookie_matching():
    extractor = CookieExtractor("google.com")
    assert extractor._matches_domain("google.com")
    assert extractor._matches_domain(".google.com")
    assert extractor._matches_domain("mail.google.com")
    assert not extractor._matches_domain("bing.com")

def test_parse_cookie_line():
    extractor = CookieExtractor("google.com")
    line = "google.com\tTRUE\t/\tFALSE\t1700000000\tname\tvalue"
    parsed = extractor.parse_cookie_line(line)
    assert parsed["domain"] == "google.com"
    assert parsed["name"] == "name"
    assert parsed["value"] == "value"

def test_extract_from_file(tmp_path):
    log_file = tmp_path / "cookies.txt"
    log_file.write_text(
        "google.com\tTRUE\t/\tFALSE\t1700000000\tG1\tV1\n"
        "youtube.com\tTRUE\t/\tFALSE\t1700000000\tY1\tV2\n"
        ".google.com\tTRUE\t/\tFALSE\t1700000000\tG2\tV3\n"
    )

    extractor = CookieExtractor("google.com")
    matches = extractor.extract_from_file(log_file)
    assert len(matches) == 2
    assert "G1" in matches[0]
    assert "G2" in matches[1]
