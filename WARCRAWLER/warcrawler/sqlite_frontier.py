"""Shared SQLite frontier for multi-process (and, with care, multi-machine)
work-stealing.

How it works
------------
* All candidate URLs live in the `frontier` table with a `state`
  (pending -> claimed -> done/error).
* Claiming is atomic: each `next()` opens a BEGIN IMMEDIATE transaction (which
  takes SQLite's write lock, serializing all processes), selects one ready
  pending URL, flips it to `claimed`, and commits. No two workers — in any
  process — can claim the same URL.
* Politeness is coordinated in the DB. A `hosts` table holds a per-host
  `next_available` timestamp and current `delay`. A URL is only claimable when
  its host's `next_available <= now` AND the number of currently `claimed`
  rows for that host is below the per-host concurrency cap. On claim we push
  `next_available` forward by the (jittered) delay. This makes N processes
  behave like one polite crawler per host.
* Adaptive backoff lives in `hosts.delay`: raised on block, decayed on success.
* Work-stealing / crash recovery: a `claimed` row whose timestamp is older than
  `fleet.lease` seconds is reset to `pending` and re-stealable, so a dead
  worker's URLs are picked up by others.
* Termination is global: a process stops only when no `pending` or `claimed`
  rows remain anywhere.

Each process uses its OWN connection (separate from Storage's) so their
transactions never entangle. WAL + busy_timeout make same-machine concurrency
robust. Over a network filesystem set fleet.network_fs (best-effort; see README).
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Optional, Tuple

import aiosqlite

from .config import JobConfig
from .logutil import get_logger
from .storage import Storage
from .urlutil import ScopeFilter, get_host

log = get_logger()

_HOSTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS hosts (
    host           TEXT PRIMARY KEY,
    next_available REAL NOT NULL DEFAULT 0,
    delay          REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_frontier_host_state ON frontier(host, state);
CREATE INDEX IF NOT EXISTS idx_frontier_state_added ON frontier(state, added_at);
"""


class SQLiteFrontier:
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
        self._capped = False
        self.lease = float(cfg.fleet.lease)
        self.network_fs = bool(cfg.fleet.network_fs)
        self.db_path = storage.db_path
        self._db: Optional[aiosqlite.Connection] = None
        # All worker coroutines in a process share one connection, so a
        # transaction is connection-global. This lock keeps their short
        # BEGIN..COMMIT sections from interleaving; the slow fetching happens
        # outside it. Cross-process safety comes from BEGIN IMMEDIATE +
        # busy_timeout, not this lock.
        self._lock = asyncio.Lock()
        self._last_reclaim = 0.0
        self._cached_pending = 0
        self._cached_inflight = 0

    async def open(self) -> None:
        # isolation_level=None -> autocommit, so explicit BEGIN IMMEDIATE works.
        self._db = await aiosqlite.connect(str(self.db_path), isolation_level=None)
        await self._db.execute(
            "PRAGMA journal_mode=DELETE;" if self.network_fs else "PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        await self._db.execute("PRAGMA busy_timeout=15000;")
        await self._db.executescript(_HOSTS_SCHEMA)
        # Self-heal: recover claims left stale by a previous run/crash.
        await self._db.execute(
            "UPDATE frontier SET state='pending' WHERE state='claimed' AND updated_at < ?",
            (time.time() - self.lease,))

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()

    # ---- population ------------------------------------------------------
    async def add(self, url: str, depth: int, persist: bool = True,
                  priority: int = 0) -> bool:
        if depth > self.max_depth:
            return False
        if not self.scope.allowed(url):
            return False
        host = get_host(url)
        now = time.time()
        async with self._lock:
            cur = await self._db.execute(
                "INSERT OR IGNORE INTO frontier(url,host,depth,priority,state,added_at,updated_at) "
                "VALUES(?,?,?,?,'pending',?,?)", (url, host, depth, int(priority), now, now))
            await self._db.execute(
                "INSERT OR IGNORE INTO hosts(host,next_available,delay) VALUES(?,0,?)",
                (host, self.base_delay))
            return cur.rowcount > 0

    def add_loaded(self, url: str, depth: int) -> None:
        # No-op: pending rows already live in the shared DB and are claimed on
        # demand. Present for interface parity with the in-memory frontier.
        return None

    # ---- scheduling ------------------------------------------------------
    async def _delay_for(self, host: str) -> float:
        async with self._db.execute("SELECT delay FROM hosts WHERE host=?", (host,)) as cur:
            row = await cur.fetchone()
        d = (row[0] if row else self.base_delay) or 0.0
        if self.jitter and d > 0:
            d = d * (1.0 + random.uniform(-self.jitter, self.jitter))
        return max(0.0, d)

    async def next(self) -> Optional[Tuple[str, int, str]]:
        async with self._lock:
            return await self._next_locked()

    async def _next_locked(self) -> Optional[Tuple[str, int, str]]:
        now = time.time()
        await self._maybe_reclaim(now)
        await self._db.execute("BEGIN IMMEDIATE")
        try:
            # Hard global cap: never let claimed+done exceed max_pages. Because
            # this runs inside BEGIN IMMEDIATE (serialized across processes),
            # the count is consistent and the cap is exact — no overshoot.
            if self.max_pages:
                async with self._db.execute(
                        "SELECT COUNT(*) FROM frontier WHERE state IN ('claimed','done')") as ccur:
                    processed = (await ccur.fetchone())[0]
                if processed >= self.max_pages:
                    self._capped = True
                    await self._db.execute("COMMIT")
                    return None
            async with self._db.execute(
                """
                SELECT f.url, f.depth, f.host
                FROM frontier f
                JOIN hosts h ON h.host = f.host
                WHERE f.state='pending'
                  AND h.next_available <= ?
                  AND (SELECT COUNT(*) FROM frontier c
                       WHERE c.host = f.host AND c.state='claimed') < ?
                ORDER BY f.priority DESC, f.added_at ASC
                LIMIT 1
                """, (now, self.per_host_conc)) as cur:
                row = await cur.fetchone()
            if row is None:
                await self._db.execute("COMMIT")
                return None
            url, depth, host = row[0], row[1], row[2]
            delay = await self._delay_for(host)
            await self._db.execute(
                "UPDATE frontier SET state='claimed', updated_at=? WHERE url=?", (now, url))
            await self._db.execute(
                "UPDATE hosts SET next_available=? WHERE host=?", (now + delay, host))
            await self._db.execute("COMMIT")
            return url, depth, host
        except Exception:
            try:
                await self._db.execute("ROLLBACK")
            except Exception:
                pass
            raise

    async def complete(self, host: str, url: str, state: str,
                       blocked: bool = False) -> None:
        now = time.time()
        async with self._lock:
            new_delay = None
            if self.adaptive:
                async with self._db.execute("SELECT delay FROM hosts WHERE host=?", (host,)) as cur:
                    row = await cur.fetchone()
                cur_delay = (row[0] if row else self.base_delay) or 0.0
                if blocked:
                    new_delay = min(120.0, max(self.base_delay, cur_delay * 2) + 1.0)
                else:
                    new_delay = max(self.base_delay, cur_delay * 0.9)
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                await self._db.execute(
                    "UPDATE frontier SET state=?, updated_at=? WHERE url=?", (state, now, url))
                if new_delay is not None:
                    await self._db.execute(
                        "UPDATE hosts SET delay=? WHERE host=?", (new_delay, host))
                await self._db.execute("COMMIT")
            except Exception:
                try:
                    await self._db.execute("ROLLBACK")
                except Exception:
                    pass
                raise

    async def note_crawl_delay(self, host: str, delay: Optional[float]) -> None:
        if delay and delay > 0:
            async with self._lock:
                await self._db.execute(
                    "UPDATE hosts SET delay=MAX(delay, ?) WHERE host=?", (float(delay), host))

    async def _maybe_reclaim(self, now: float) -> None:
        if now - self._last_reclaim < max(5.0, self.lease / 4.0):
            return
        self._last_reclaim = now
        await self._db.execute(
            "UPDATE frontier SET state='pending' WHERE state='claimed' AND updated_at < ?",
            (now - self.lease,))

    async def remaining(self) -> int:
        async with self._lock:
            async with self._db.execute(
                    "SELECT COUNT(*) FROM frontier WHERE state IN ('pending','claimed')") as cur:
                self._cached_pending = (await cur.fetchone())[0]
        return self._cached_pending

    @property
    def capped(self) -> bool:
        return self._capped

    # ---- status (approximate, for the dashboard) -------------------------
    @property
    def pending(self) -> int:
        return self._cached_pending

    @property
    def inflight_total(self) -> int:
        return self._cached_inflight
