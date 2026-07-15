"""Focused crawling: score a candidate URL by keyword/pattern relevance so the
frontier can crawl the most relevant pages first.

Signals available before fetching: the link's anchor text (weighted higher)
and the URL string. Focus terms come from focus.keywords / focus.patterns and,
by default, the extract.watchlist regexes.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .config import FocusConfig


class Scorer:
    def __init__(self, cfg: FocusConfig, watchlist: Optional[List[str]] = None):
        self._res = []
        for term in cfg.keywords:
            if term:
                self._res.append(re.compile(re.escape(term), re.I))
        pats = list(cfg.patterns)
        if cfg.use_watchlist and watchlist:
            pats += list(watchlist)
        for pat in pats:
            try:
                self._res.append(re.compile(pat, re.I))
            except re.error:
                continue
        self.anchor_weight = float(cfg.anchor_weight)
        self.url_weight = float(cfg.url_weight)

    @property
    def active(self) -> bool:
        return bool(self._res)

    def score(self, anchor_text: str, url: str) -> int:
        if not self._res:
            return 0
        a = sum(1 for r in self._res if r.search(anchor_text or ""))
        u = sum(1 for r in self._res if r.search(url or ""))
        return int(round(self.anchor_weight * a + self.url_weight * u))
