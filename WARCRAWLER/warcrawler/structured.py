"""Generic structured-data extraction: JSON-LD (schema.org), OpenGraph,
Twitter cards, standard <meta> tags, and <link rel=canonical>.

Produces a consolidated dict plus a normalized `summary` of the fields people
actually want (title, author, dates, description, image, site, type) — no
per-site rules required. Operates on an lxml HTML tree; degrades to {} safely.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

_JSONLD_CAP = 25


def extract_structured(tree) -> Dict[str, Any]:
    if tree is None:
        return {}
    jsonld = _jsonld(tree)
    og, tw, meta = _metas(tree)
    canonical = _canonical(tree)
    summary = _summarize(jsonld, og, tw, meta)
    out: Dict[str, Any] = {}
    if jsonld:
        out["jsonld"] = jsonld[:_JSONLD_CAP]
    if og:
        out["opengraph"] = og
    if tw:
        out["twitter"] = tw
    if meta:
        out["meta"] = meta
    if canonical:
        out["canonical"] = canonical
    if summary:
        out["summary"] = summary
    return out


def _jsonld(tree) -> List[dict]:
    out: List[dict] = []
    for el in tree.iter("script"):
        if "ld+json" not in (el.get("type") or "").lower():
            continue
        raw = (el.text or "").strip() or "".join(el.itertext()).strip()
        if not raw:
            continue
        data = None
        for attempt in (raw, raw.rstrip(";").strip()):
            try:
                data = json.loads(attempt)
                break
            except Exception:
                continue
        if data is None:
            continue
        for obj in (data if isinstance(data, list) else [data]):
            out.extend(_flatten_graph(obj))
    return out


def _flatten_graph(d: Any) -> List[dict]:
    if not isinstance(d, dict):
        return []
    if isinstance(d.get("@graph"), list):
        return [g for g in d["@graph"] if isinstance(g, dict)]
    return [d]


def _metas(tree):
    og: Dict[str, str] = {}
    tw: Dict[str, str] = {}
    meta: Dict[str, str] = {}
    for el in tree.iter("meta"):
        content = el.get("content")
        if content is None:
            continue
        prop = (el.get("property") or "").lower()
        name = (el.get("name") or "").lower()
        if prop.startswith("og:") or prop.startswith("article:") or prop.startswith("product:"):
            og[prop] = content
        elif name.startswith("twitter:"):
            tw[name] = content
        elif name:
            meta[name] = content
        elif prop:
            meta[prop] = content
    return og, tw, meta


def _canonical(tree) -> Optional[str]:
    for el in tree.iter("link"):
        if "canonical" in (el.get("rel") or "").lower() and el.get("href"):
            return el.get("href").strip()
    return None


def _name_of(val: Any) -> Optional[str]:
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return val.get("name") or val.get("@id")
    if isinstance(val, list) and val:
        return _name_of(val[0])
    return None


def _first(*vals):
    for v in vals:
        if v:
            return v
    return None


def _summarize(jsonld, og, tw, meta) -> Dict[str, Any]:
    jl: Dict[str, Any] = {}
    jtype = None
    for obj in jsonld:
        if jtype is None and obj.get("@type"):
            jtype = obj.get("@type")
        for k in ("headline", "name", "author", "datePublished", "dateModified",
                  "description", "image"):
            if k in obj and k not in jl:
                jl[k] = obj[k]
    summary = {
        "title": _first(og.get("og:title"), jl.get("headline"), jl.get("name")),
        "author": _name_of(_first(jl.get("author"), meta.get("author"),
                                   og.get("article:author"))),
        "published": _first(jl.get("datePublished"), og.get("article:published_time"),
                            meta.get("article:published_time")),
        "modified": _first(jl.get("dateModified"), og.get("article:modified_time")),
        "site_name": og.get("og:site_name"),
        "description": _first(og.get("og:description"), meta.get("description"),
                              jl.get("description")),
        "image": _name_of(_first(og.get("og:image"), tw.get("twitter:image"),
                                 jl.get("image"))),
        "type": _first(og.get("og:type"), jtype),
    }
    return {k: v for k, v in summary.items() if v}
