"""In-memory URL frontier: host-bucketed scheduling with per-host politeness
and adaptive backoff. Persistence/resume is delegated to Storage.

Implements the same async interface as SQLiteFrontier (open / add / next /
complete / remaining / note_crawl_delay) so the engine can swap backends.
This backend is single-process; for multi-process work-stealing use
fleet.shared (SQLiteFrontier).
"""
from __future__ import annotations

import heapq
import random
import time
from typing import Dict, List, Optional, Tuple

from .config import JobConfig
from .storage import Storage
from .urlutil import ScopeFilter, get_host


class Frontier:
    def __init__(self, cfg: JobConfig, storage: Storage, scope: ScopeFilter):
        self.cfg = cfg
        self.storage = storage
        self.scope = scope
        self.base_delay = float(cfg.throttle.per_host_delay)
        self.per_host_conc = max(1, int(cfg.throttle.per_host_concurrency))
        self.jitter = float(cfg.throttle.jitter)
        self.adaptive = bool(cfg.throttle.adaptive_backoff)
        self.max_depth = int(cfg.scope.max_depth)

        self.max_pages = int(cfg.scope.max_pages) if cfg.scope.max_pages else 0

        # Each host bucket is a min-heap of (-priority, seq, url, depth) so the
        # highest-priority URL pops first, ties broken FIFO by insertion seq.
        self.buckets: Dict[str, List[Tuple[int, int, str, int]]] = {}
        self.host_order = []  # type: list
        self._ptr = 0
        self.next_time: Dict[str, float] = {}
        self.inflight: Dict[str, int] = {}
        self.host_delay: Dict[str, float] = {}
        self._pending = 0
        self._seq = 0
        # Hard cap accounting: baseline = pages already 'done' from prior runs,
        # dispatched = URLs handed out this run. Dispatch stops once their sum
        # reaches max_pages, so the crawl never overshoots.
        self.baseline = 0
        self.dispatched = 0

    # ---- lifecycle (no-ops; state lives in Storage) ----------------------
    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def clear(self) -> None:
        """Drop in-memory scheduling state (not storage). Used between monitor
        passes so a reload from storage doesn't stack duplicates."""
        self.buckets.clear()
        self.host_order = []
        self._ptr = 0
        self.next_time.clear()
        self.inflight.clear()
        self.host_delay.clear()
        self._pending = 0

    # ---- population ------------------------------------------------------
    async def add(self, url: str, depth: int, persist: bool = True,
                  priority: int = 0) -> bool:
        if depth > self.max_depth:
            return False
        if not self.scope.allowed(url):
            return False
        host = get_host(url)
        if persist:
            is_new = await self.storage.add_frontier(url, host, depth, priority)
            if not is_new:
                return False
        self._push(host, url, depth, priority)
        return True

    def add_loaded(self, url: str, depth: int, priority: int = 0) -> None:
        """Re-queue a URL already present in storage (resume path)."""
        self._push(get_host(url), url, depth, priority)

    def set_baseline(self, n: int) -> None:
        self.baseline = int(n)

    def reset_dispatched(self) -> None:
        self.dispatched = 0

    @property
    def capped(self) -> bool:
        return bool(self.max_pages and (self.baseline + self.dispatched) >= self.max_pages)

    def _push(self, host: str, url: str, depth: int, priority: int = 0) -> None:
        if host not in self.buckets:
            self.buckets[host] = []
            self.host_order.append(host)
            self.host_delay[host] = self.base_delay
        heapq.heappush(self.buckets[host], (-int(priority), self._seq, url, depth))
        self._seq += 1
        self._pending += 1

    # ---- scheduling ------------------------------------------------------
    def _delay_for(self, host: str) -> float:
        d = self.host_delay.get(host, self.base_delay)
        if self.jitter and d > 0:
            d = d * (1.0 + random.uniform(-self.jitter, self.jitter))
        return max(0.0, d)

    def _get_ready(self, now: float) -> Optional[Tuple[str, int, str]]:
        if self.capped:
            return None
        n = len(self.host_order)
        for _ in range(n):
            if not self.host_order:
                break
            host = self.host_order[self._ptr % len(self.host_order)]
            self._ptr += 1
            bucket = self.buckets.get(host)
            if not bucket:
                continue
            if self.inflight.get(host, 0) >= self.per_host_conc:
                continue
            if now < self.next_time.get(host, 0.0):
                continue
            _, _, url, depth = heapq.heappop(bucket)
            self._pending -= 1
            self.dispatched += 1
            self.inflight[host] = self.inflight.get(host, 0) + 1
            self.next_time[host] = now + self._delay_for(host)
            return url, depth, host
        return None

    async def next(self) -> Optional[Tuple[str, int, str]]:
        return self._get_ready(time.time())

    async def complete(self, host: str, url: str, state: str,
                       blocked: bool = False) -> None:
        await self.storage.mark(url, state)
        if host in self.inflight:
            self.inflight[host] = max(0, self.inflight[host] - 1)
        if not self.adaptive:
            return
        base = self.host_delay.get(host, self.base_delay)
        if blocked:
            self.host_delay[host] = min(120.0, max(self.base_delay, base * 2) + 1.0)
        else:
            self.host_delay[host] = max(self.base_delay, base * 0.9)

    async def note_crawl_delay(self, host: str, delay: Optional[float]) -> None:
        if delay and delay > self.host_delay.get(host, 0.0):
            self.host_delay[host] = float(delay)

    async def remaining(self) -> int:
        return self._pending + self.inflight_total

    # ---- status ----------------------------------------------------------
    @property
    def pending(self) -> int:
        return self._pending

    @property
    def inflight_total(self) -> int:
        return sum(self.inflight.values())
