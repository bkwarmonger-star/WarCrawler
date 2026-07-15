"""Sitemap discovery and parsing (XML sitemaps + sitemap-index files).

Dependency-free (stdlib xml + gzip). Handles gzipped sitemaps and namespaced
XML by matching on local tag names, per the sitemaps.org spec.
"""
from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urlsplit


def _maybe_gunzip(data: bytes) -> bytes:
    if data[:2] == b"\x1f\x8b":  # gzip magic
        try:
            return gzip.decompress(data)
        except Exception:
            return data
    return data


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap(data: bytes) -> Tuple[List[str], List[str]]:
    """Return (page_urls, sub_sitemap_urls).

    A <urlset> yields page URLs; a <sitemapindex> yields sub-sitemap URLs.
    """
    data = _maybe_gunzip(data)
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return [], []
    locs = [el.text.strip() for el in root.iter()
            if _local(el.tag) == "loc" and el.text and el.text.strip()]
    if _local(root.tag) == "sitemapindex":
        return [], locs
    return locs, []  # urlset (or anything else) -> treat as page URLs


def sitemaps_from_robots(robots_text: str) -> List[str]:
    out = []
    for line in (robots_text or "").splitlines():
        if ":" in line and line.split(":", 1)[0].strip().lower() == "sitemap":
            url = line.split(":", 1)[1].strip()
            if url:
                out.append(url)
    return out


def default_sitemap(base_url: str) -> str:
    p = urlsplit(base_url)
    return "{}://{}/sitemap.xml".format(p.scheme, p.netloc)


FetchBytes = Callable[[str], Awaitable[Optional[bytes]]]


async def gather_urls(fetch_bytes: FetchBytes, sitemap_urls: List[str],
                      max_urls: int = 5000, max_maps: int = 100) -> List[str]:
    """BFS across sitemap(-index) files, collecting page URLs up to a cap."""
    out: List[str] = []
    seen = set()
    queue = list(dict.fromkeys(sitemap_urls))
    maps = 0
    while queue and len(out) < max_urls and maps < max_maps:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        maps += 1
        data = await fetch_bytes(sm)
        if not data:
            continue
        urls, subs = parse_sitemap(data)
        out.extend(urls)
        for s in subs:
            if s not in seen:
                queue.append(s)
    return out[:max_urls]
