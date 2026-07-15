"""Near-duplicate detection via 64-bit SimHash with banded LSH buckets.

Pure-Python and dependency-free so it is fully portable. Exact-duplicate
detection is handled separately by content hashing in storage.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Set, Tuple

_TOKEN = re.compile(r"[a-z0-9]{2,}")
_MASK64 = (1 << 64) - 1


def _hash64(token: str) -> int:
    d = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(d, "big")


def simhash(text: str, shingle: int = 3) -> int:
    """Compute a 64-bit SimHash over word shingles of the text."""
    tokens = _TOKEN.findall((text or "").lower())
    if not tokens:
        return 0
    if shingle > 1 and len(tokens) >= shingle:
        grams = [" ".join(tokens[i:i + shingle]) for i in range(len(tokens) - shingle + 1)]
    else:
        grams = tokens
    weights = [0] * 64
    for g in grams:
        h = _hash64(g)
        for i in range(64):
            if (h >> i) & 1:
                weights[i] += 1
            else:
                weights[i] -= 1
    out = 0
    for i in range(64):
        if weights[i] > 0:
            out |= (1 << i)
    return out & _MASK64


def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & _MASK64).count("1")


class DedupIndex:
    """Banded SimHash index. 4 bands of 16 bits give fast candidate lookup."""

    def __init__(self, max_distance: int = 3):
        self.max_distance = max_distance
        self._bands: List[Dict[int, List[int]]] = [dict() for _ in range(4)]
        self._all: Set[int] = set()

    def _band_keys(self, h: int) -> List[Tuple[int, int]]:
        return [(b, (h >> (b * 16)) & 0xFFFF) for b in range(4)]

    def add(self, h: int) -> None:
        if h in self._all:
            return
        self._all.add(h)
        for b, key in self._band_keys(h):
            self._bands[b].setdefault(key, []).append(h)

    def is_duplicate(self, h: int) -> bool:
        if h == 0:
            return False
        if h in self._all:
            return True
        seen: Set[int] = set()
        for b, key in self._band_keys(h):
            for cand in self._bands[b].get(key, ()):  # candidates share a band
                if cand in seen:
                    continue
                seen.add(cand)
                if hamming(h, cand) <= self.max_distance:
                    return True
        return False

    def add_if_new(self, h: int) -> bool:
        """Return True if h is new (and add it); False if it is a near-dup."""
        if self.is_duplicate(h):
            return False
        self.add(h)
        return True

    def __len__(self) -> int:
        return len(self._all)
