"""Non-HTML document handling: sniff the content kind and pull plain text out
of PDFs and text files so they become searchable / watchlist-scannable.

PDF support is optional (pypdf); if it isn't installed, PDFs are still fetched
and archived — they just won't have extracted text. Everything else is stdlib.
"""
from __future__ import annotations

import re
from typing import Tuple

from .logutil import get_logger

log = get_logger()

_MAX_PDF_PAGES = 200  # bound cost on huge PDFs


def sniff_kind(content_type: str, raw: bytes) -> str:
    """Return one of: html, pdf, text, other."""
    ct = (content_type or "").lower().split(";")[0].strip()  # drop ;charset=...
    head = raw[:1024].lstrip() if raw else b""
    if raw[:5] == b"%PDF-" or ct == "application/pdf" or ct.endswith("/pdf"):
        return "pdf"
    if "html" in ct or "xhtml" in ct:
        return "html"
    if not ct and head[:1] == b"<":
        return "html"
    _text_types = ("application/json", "application/xml", "application/rss+xml",
                   "application/atom+xml", "application/xhtml+xml")
    if ct.startswith("text/") or ct in _text_types or ct.endswith("+xml") or ct.endswith("+json"):
        return "text"
    if not ct and head and _looks_texty(head):
        return "text"
    return "other"


def _looks_texty(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    # Mostly printable ASCII/UTF-8?
    printable = sum(1 for b in head if 9 <= b <= 13 or 32 <= b <= 126 or b >= 128)
    return printable / max(1, len(head)) > 0.85


def extract_text(kind: str, raw: bytes, content_type: str = "") -> Tuple[str, str]:
    """Return (title, text) for pdf/text content; ("","") otherwise or on error."""
    if kind == "pdf":
        return _extract_pdf(raw)
    if kind == "text":
        return "", _decode(raw, content_type)
    return "", ""


def _decode(raw: bytes, content_type: str = "") -> str:
    m = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    for enc in filter(None, [m.group(1) if m else None, "utf-8", "latin-1"]):
        try:
            return raw.decode(enc, errors="replace")
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_pdf(raw: bytes) -> Tuple[str, str]:
    try:
        import io
        from pypdf import PdfReader
    except Exception:
        log.debug("pypdf not installed; PDF stored without extracted text")
        return "", ""
    try:
        reader = PdfReader(io.BytesIO(raw))
        title = ""
        try:
            if reader.metadata and reader.metadata.title:
                title = str(reader.metadata.title)
        except Exception:
            title = ""
        parts = []
        for i, page in enumerate(reader.pages):
            if i >= _MAX_PDF_PAGES:
                break
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip()
        return title, text
    except Exception as exc:
        log.debug("PDF parse failed: %s", exc)
        return "", ""
