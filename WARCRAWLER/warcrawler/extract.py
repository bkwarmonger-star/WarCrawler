"""Content and link extraction pipeline.

Uses lxml for robust HTML parsing and link discovery, trafilatura (optional)
for main-article extraction / language / metadata, and regex watchlists plus
CSS/XPath rules for structured capture. Degrades gracefully if trafilatura is
not installed.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from .config import ExtractConfig
from .dedup import simhash
from .logutil import get_logger
from .structured import extract_structured
from .urlutil import resolve_links

log = get_logger()

try:
    import lxml.html as lxml_html
    from lxml import etree
    _HAS_LXML = True
except Exception:  # pragma: no cover
    _HAS_LXML = False

try:
    import trafilatura
    _HAS_TRAFILATURA = True
except Exception:  # pragma: no cover
    _HAS_TRAFILATURA = False

try:
    from selectolax.parser import HTMLParser as _SelectoParser  # noqa: F401
    _HAS_SELECTOLAX = True
except Exception:
    _HAS_SELECTOLAX = False

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_HREF_RE = re.compile(r"""href\s*=\s*["']?([^"'\s>]+)""", re.I)


def _decode(raw: bytes, content_type: str = "") -> str:
    charset = None
    m = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    if m:
        charset = m.group(1)
    for enc in filter(None, [charset, "utf-8", "latin-1"]):
        try:
            return raw.decode(enc, errors="replace")
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


class Extractor:
    def __init__(self, cfg: ExtractConfig):
        self.cfg = cfg
        flags = re.I if cfg.watchlist_ignore_case else 0
        self._watch = [(p, re.compile(p, flags)) for p in cfg.watchlist]

    def extract(self, url: str, raw: bytes, content_type: str
                ) -> Tuple[Dict[str, Any], List[str], str]:
        """Return (record_fields, out_links, main_text)."""
        html = _decode(raw, content_type)
        title = ""
        links: List[str] = []
        main_text = ""
        lang = None
        extracted: Dict[str, Any] = {}
        structured: Dict[str, Any] = {}
        link_anchors: Dict[str, str] = {}   # canonical url -> anchor text (transient)

        tree = None
        if _HAS_LXML:
            try:
                tree = lxml_html.fromstring(html)
            except Exception:
                tree = None

        if tree is not None:
            t = tree.findtext(".//title")
            title = (t or "").strip()
            if self.cfg.follow_links:
                for el in tree.iter("a"):
                    href = el.get("href")
                    if not href:
                        continue
                    resolved = resolve_links(url, [href], strip_tracking=True)
                    if not resolved:
                        continue
                    c = resolved[0]
                    if c not in link_anchors:
                        txt = re.sub(r"\s+", " ", (el.text_content() or "")).strip()
                        link_anchors[c] = txt[:200]
                links = list(link_anchors.keys())
            extracted = self._apply_rules(tree)
            if self.cfg.structured_data:
                structured = extract_structured(tree)
                if not title:
                    title = (structured.get("summary") or {}).get("title") or ""
        else:
            m = _TITLE_RE.search(html)
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).strip()
            if self.cfg.follow_links:
                links = resolve_links(url, _HREF_RE.findall(html), strip_tracking=True)

        if self.cfg.extract_content and _HAS_TRAFILATURA:
            try:
                main_text = trafilatura.extract(
                    html, include_comments=False, include_tables=True,
                    favor_recall=True) or ""
            except Exception:
                main_text = ""
        if not main_text and tree is not None:
            main_text = re.sub(r"\s+", " ", tree.text_content()).strip()

        if self.cfg.detect_language and main_text:
            lang = _detect_language(html, main_text)
        if not lang:
            loc = (structured.get("opengraph") or {}).get("og:locale")
            if loc:
                lang = loc.split("_")[0][:5]

        text_for_hash = main_text or html
        content_hash = hashlib.blake2b(
            text_for_hash.encode("utf-8", "replace"), digest_size=16).hexdigest()
        sh = simhash(main_text or title or html)

        matches = self._match_watchlist(main_text or "", html)

        record = {
            "title": title[:500],
            "lang": lang,
            "content_hash": content_hash,
            "simhash": sh,
            "matches": matches,
            "extracted": extracted,
            "structured": structured,
            "_anchors": link_anchors,
        }
        return record, links, main_text

    def finalize(self, title: str, text: str) -> Dict[str, Any]:
        """Compute content_hash / simhash / watchlist / language from plain text.

        Used for non-HTML documents (PDF, plain text) that have no DOM.
        """
        base = text or title or ""
        content_hash = hashlib.blake2b(
            base.encode("utf-8", "replace"), digest_size=16).hexdigest()
        sh = simhash(text or title or "")
        matches = self._match_watchlist(text or "", text or "")
        lang = None
        if self.cfg.detect_language and text:
            lang = _detect_language("", text)
        return {"title": (title or "")[:500], "lang": lang,
                "content_hash": content_hash, "simhash": sh,
                "matches": matches, "extracted": {}}

    def _apply_rules(self, tree) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for name, sel in (self.cfg.css_rules or {}).items():
            try:
                nodes = tree.cssselect(sel)
                out[name] = [self._node_text(n) for n in nodes][:50]
            except Exception as exc:
                log.debug("css rule %s failed: %s", name, exc)
        for name, xp in (self.cfg.xpath_rules or {}).items():
            try:
                res = tree.xpath(xp)
                out[name] = [self._node_text(n) for n in res][:50]
            except Exception as exc:
                log.debug("xpath rule %s failed: %s", name, exc)
        return out

    @staticmethod
    def _node_text(node) -> str:
        if isinstance(node, str):
            return node.strip()
        try:
            return re.sub(r"\s+", " ", node.text_content()).strip()
        except Exception:
            return str(node)

    def _match_watchlist(self, text: str, html: str) -> List[Dict[str, Any]]:
        hits: List[Dict[str, Any]] = []
        haystack = text if text else html
        for pattern, rx in self._watch:
            found = rx.findall(haystack)
            if found:
                sample = found[:5]
                hits.append({"pattern": pattern, "count": len(found),
                             "samples": [str(s)[:120] for s in sample]})
        return hits


def _detect_language(html: str, text: str) -> Optional[str]:
    if _HAS_TRAFILATURA:
        try:
            meta = trafilatura.metadata.extract_metadata(html)
            if meta and getattr(meta, "language", None):
                return meta.language
        except Exception:
            pass
    # Lightweight fallback: py3langid / langid if present.
    for modname in ("py3langid", "langid"):
        try:
            mod = __import__(modname)
            return mod.classify(text[:2000])[0]
        except Exception:
            continue
    return None
