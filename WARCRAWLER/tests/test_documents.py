from warcrawler.documents import sniff_kind, extract_text
from warcrawler.config import ExtractConfig
from warcrawler.extract import Extractor


def test_sniff_kind():
    assert sniff_kind("text/html; charset=utf-8", b"<!doctype html>") == "html"
    assert sniff_kind("application/pdf", b"%PDF-1.7 ...") == "pdf"
    assert sniff_kind("application/octet-stream", b"%PDF-1.4\n...") == "pdf"  # magic wins
    assert sniff_kind("text/plain", b"just some text") == "text"
    assert sniff_kind("application/json", b'{"a":1}') == "text"
    assert sniff_kind("image/png", b"\x89PNG\r\n\x1a\n") == "other"
    assert sniff_kind("", b"<html>") == "html"          # sniffed from body
    assert sniff_kind("", b"plain readable text here") == "text"


def test_extract_text_plaintext():
    title, text = extract_text("text", b"leak dump: user@example.com", "text/plain")
    assert title == ""
    assert "user@example.com" in text


def test_extract_text_other_is_empty():
    assert extract_text("other", b"\x00\x01\x02", "image/png") == ("", "")


def test_finalize_makes_document_searchable_and_matchable():
    ex = Extractor(ExtractConfig(watchlist=[r"CVE-\d{4}-\d+"]))
    rec = ex.finalize("Advisory", "Impact: CVE-2026-12345 affects the service.")
    assert rec["title"] == "Advisory"
    assert rec["content_hash"]
    assert 0 <= rec["simhash"] < (1 << 64)
    assert rec["matches"] and rec["matches"][0]["pattern"] == r"CVE-\d{4}-\d+"
