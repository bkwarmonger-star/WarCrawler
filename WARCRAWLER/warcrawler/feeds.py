"""RSS 2.0 / Atom / RDF feed parsing and HTML feed autodiscovery.

Dependency-free parsing (stdlib xml). Matches on local tag names so namespaces
(Atom, Dublin Core, etc.) don't matter. Used to seed the frontier with the
freshest items — the natural companion to sitemap ingestion for monitoring.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Dict, List
from urllib.parse import urljoin


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(data: bytes) -> List[Dict[str, str]]:
    """Return a list of items: {title, link, published, summary, id}."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    items: List[Dict[str, str]] = []
    for el in root.iter():
        if _local(el.tag) in ("item", "entry"):
            it = _parse_item(el)
            if it.get("link"):
                items.append(it)
    return items


def _parse_item(el) -> Dict[str, str]:
    d = {"title": "", "link": "", "published": "", "summary": "", "id": ""}
    for c in el:
        ln = _local(c.tag)
        if ln == "title" and not d["title"]:
            d["title"] = (c.text or "").strip()
        elif ln == "link":
            href = c.get("href")
            if href:  # Atom-style <link href=... rel=...>
                rel = (c.get("rel") or "alternate").lower()
                if rel == "alternate" or not d["link"]:
                    d["link"] = href.strip()
            elif c.text and not d["link"]:  # RSS-style <link>text</link>
                d["link"] = c.text.strip()
        elif ln in ("pubdate", "published", "updated", "date") and not d["published"]:
            d["published"] = (c.text or "").strip()
        elif ln in ("description", "summary", "content") and not d["summary"]:
            d["summary"] = (c.text or "").strip()[:500]
        elif ln in ("guid", "id") and not d["id"]:
            d["id"] = (c.text or "").strip()
    return d


def feeds_from_html(body: bytes, base_url: str) -> List[str]:
    """Autodiscover feed URLs from a page's <link rel=alternate> tags."""
    try:
        import lxml.html as LH
        tree = LH.fromstring(body)
    except Exception:
        return []
    out: List[str] = []
    for el in tree.iter("link"):
        rel = (el.get("rel") or "").lower()
        typ = (el.get("type") or "").lower()
        href = el.get("href")
        if href and "alternate" in rel and ("rss" in typ or "atom" in typ or "xml" in typ):
            out.append(urljoin(base_url, href.strip()))
    return out
