"""Redis-backed frontier for robust multi-machine work-stealing.

Same async interface as Frontier / SQLiteFrontier, so it drops into the engine
unchanged. All the tricky bits (claim, complete, reclaim, cap) run as atomic
Lua scripts inside Redis, so many machines can share one queue safely.

Data model (all keys prefixed `wc:{namespace}:`)
  seen                SET   of URLs ever enqueued (global dedup)
  host:{host}         LIST  of pending "depth\\turl" items for a host (FIFO)
  hosts_ready         ZSET  host -> next_available epoch (claim only when <= now)
  host_delay:{host}   STR   current politeness delay for the host (adaptive)
  inflight:{host}     STR   count of in-flight claims for the host (concurrency cap)
  claims_exp          ZSET  url -> claim expiry epoch (lease; drives reclaim)
  claims_meta         HASH  url -> "host\\tdepth" (to requeue on reclaim)
  pending             STR   count of queued (not yet claimed) urls
  processed           STR   count of claimed+done urls (drives the hard cap)

Requires a single Redis instance (not Cluster) because scripts touch keys
derived from the host at runtime. Redis persistence (RDB/AOF) gives resume.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

from .config import JobConfig
from .logutil import get_logger
from .storage import Storage
from .urlutil import ScopeFilter, get_host

log = get_logger()

# ---- Lua scripts (atomic inside Redis) ------------------------------------

_ADD = """
local p = ARGV[5]
if redis.call('SADD', p..'seen', ARGV[1]) == 0 then return 0 end
-- Host queue is a ZSET scored by priority; a monotonic seq keeps equal
-- priorities FIFO (earlier seq -> higher score -> popped first by ZPOPMAX).
local seq = redis.call('INCR', p..'seq')
local score = tonumber(ARGV[6]) * 1000000000 - seq
redis.call('ZADD', p..'host:'..ARGV[3], score, ARGV[2]..'\\t'..ARGV[1])
if redis.call('ZSCORE', p..'hosts_ready', ARGV[3]) == false then
  redis.call('ZADD', p..'hosts_ready', 0, ARGV[3])
end
if redis.call('EXISTS', p..'host_delay:'..ARGV[3]) == 0 then
  redis.call('SET', p..'host_delay:'..ARGV[3], ARGV[4])
end
redis.call('INCR', p..'pending')
return 1
"""

_CLAIM = """
local p = ARGV[4]
local now = tonumber(ARGV[1])
local conc = tonumber(ARGV[2])
local lease = tonumber(ARGV[3])
local maxp = tonumber(ARGV[5])
if maxp > 0 then
  local processed = tonumber(redis.call('GET', p..'processed') or '0')
  if processed >= maxp then return {'CAPPED'} end
end
local hosts = redis.call('ZRANGEBYSCORE', p..'hosts_ready', '-inf', now, 'LIMIT', 0, 64)
for _, h in ipairs(hosts) do
  local card = redis.call('ZCARD', p..'host:'..h)
  if card == 0 then
    redis.call('ZREM', p..'hosts_ready', h)
  else
    local inf = tonumber(redis.call('GET', p..'inflight:'..h) or '0')
    if inf < conc then
      local popped = redis.call('ZPOPMAX', p..'host:'..h)  -- highest priority first
      local item = popped[1]
      local score = popped[2]
      local d, u = item:match('([^\\t]*)\\t(.*)')
      local delay = tonumber(redis.call('GET', p..'host_delay:'..h) or '0')
      redis.call('ZADD', p..'hosts_ready', now + delay, h)
      redis.call('INCR', p..'inflight:'..h)
      redis.call('ZADD', p..'claims_exp', now + lease, u)
      redis.call('HSET', p..'claims_meta', u, h..'\\t'..d..'\\t'..score)
      redis.call('DECR', p..'pending')
      if maxp > 0 then redis.call('INCR', p..'processed') end
      return {u, d, h}
    end
  end
end
return {}
"""

_COMPLETE = """
local p = ARGV[6]
redis.call('ZREM', p..'claims_exp', ARGV[1])
redis.call('HDEL', p..'claims_meta', ARGV[1])
local infkey = p..'inflight:'..ARGV[2]
local inf = tonumber(redis.call('GET', infkey) or '0')
if inf > 0 then redis.call('DECR', infkey) end
if ARGV[5] == '1' then
  local base = tonumber(ARGV[7])
  local dkey = p..'host_delay:'..ARGV[2]
  local d = tonumber(redis.call('GET', dkey) or tostring(base))
  local nd
  if ARGV[4] == '1' then nd = math.min(120, math.max(base, d * 2) + 1)
  else nd = math.max(base, d * 0.9) end
  redis.call('SET', dkey, tostring(nd))
end
if ARGV[3] == 'error' and tonumber(ARGV[8]) > 0 then
  redis.call('DECR', p..'processed')
end
return 1
"""

_RECLAIM = """
local p = ARGV[3]
local now = tonumber(ARGV[1])
local maxp = tonumber(ARGV[2])
local expired = redis.call('ZRANGEBYSCORE', p..'claims_exp', '-inf', now, 'LIMIT', 0, tonumber(ARGV[4]))
local n = 0
for _, u in ipairs(expired) do
  local meta = redis.call('HGET', p..'claims_meta', u)
  if meta then
    local h, d, sc = meta:match('([^\\t]*)\\t([^\\t]*)\\t(.*)')
    redis.call('ZADD', p..'host:'..h, tonumber(sc) or 0, d..'\\t'..u)
    if redis.call('ZSCORE', p..'hosts_ready', h) == false then
      redis.call('ZADD', p..'hosts_ready', 0, h)
    end
    local infkey = p..'inflight:'..h
    local inf = tonumber(redis.call('GET', infkey) or '0')
    if inf > 0 then redis.call('DECR', infkey) end
    redis.call('INCR', p..'pending')
    if maxp > 0 then redis.call('DECR', p..'processed') end
  end
  redis.call('ZREM', p..'claims_exp', u)
  redis.call('HDEL', p..'claims_meta', u)
  n = n + 1
end
return n
"""

_NOTE_DELAY = """
local p = ARGV[3]
local dkey = p..'host_delay:'..ARGV[1]
local cur = tonumber(redis.call('GET', dkey) or '0')
if tonumber(ARGV[2]) > cur then redis.call('SET', dkey, ARGV[2]) end
return 1
"""


def _dec(v) -> str:
    return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)


class RedisFrontier:
    def __init__(self, cfg: JobConfig, storage: Storage, scope: ScopeFilter,
                 client=None):
        self.cfg = cfg
        self.storage = storage
        self.scope = scope
        self.base_delay = float(cfg.throttle.per_host_delay)
        self.per_host_conc = max(1, int(cfg.throttle.per_host_concurrency))
        self.adaptive = bool(cfg.throttle.adaptive_backoff)
        self.max_depth = int(cfg.scope.max_depth)
        self.max_pages = int(cfg.scope.max_pages) if cfg.scope.max_pages else 0
        self.lease = float(cfg.fleet.lease)
        self.url = cfg.fleet.redis_url
        ns = cfg.fleet.namespace or cfg.name
        self.prefix = "wc:{}:".format(ns)
        self._client = client
        self._external_client = client is not None
        self._capped = False
        self._last_reclaim = 0.0
        self._cached_pending = 0
        self._cached_inflight = 0
        self._add = self._claim = self._complete = self._reclaim = self._note = None

    async def open(self) -> None:
        if self._client is None:
            import redis.asyncio as redis  # lazy; only needed for this backend
            self._client = redis.from_url(self.url)
        await self._client.ping()
        self._add = self._client.register_script(_ADD)
        self._claim = self._client.register_script(_CLAIM)
        self._complete = self._client.register_script(_COMPLETE)
        self._reclaim = self._client.register_script(_RECLAIM)
        self._note = self._client.register_script(_NOTE_DELAY)
        log.info("redis frontier connected: %s (namespace %s)", self.url, self.prefix)

    async def close(self) -> None:
        if self._client is not None and not self._external_client:
            try:
                await self._client.aclose()
            except AttributeError:  # older redis-py
                await self._client.close()

    # ---- population ------------------------------------------------------
    async def add(self, url: str, depth: int, persist: bool = True,
                  priority: int = 0) -> bool:
        if depth > self.max_depth:
            return False
        if not self.scope.allowed(url):
            return False
        host = get_host(url)
        res = await self._add(keys=[], args=[url, str(depth), host,
                                             str(self.base_delay), self.prefix,
                                             str(int(priority))])
        return int(res) == 1

    def add_loaded(self, url: str, depth: int) -> None:
        return None  # state persists in Redis; nothing to reload

    # ---- scheduling ------------------------------------------------------
    async def next(self) -> Optional[Tuple[str, int, str]]:
        now = time.time()
        await self._maybe_reclaim(now)
        res = await self._claim(keys=[], args=[str(now), str(self.per_host_conc),
                                               str(self.lease), self.prefix,
                                               str(self.max_pages)])
        if not res:
            return None
        if len(res) == 1 and _dec(res[0]) == "CAPPED":
            self._capped = True
            return None
        url, depth, host = _dec(res[0]), int(_dec(res[1])), _dec(res[2])
        return url, depth, host

    async def complete(self, host: str, url: str, state: str,
                       blocked: bool = False) -> None:
        await self._complete(keys=[], args=[
            url, host, state, "1" if blocked else "0",
            "1" if self.adaptive else "0", self.prefix,
            str(self.base_delay), str(self.max_pages)])

    async def note_crawl_delay(self, host: str, delay: Optional[float]) -> None:
        if delay and delay > 0:
            await self._note(keys=[], args=[host, str(float(delay)), self.prefix])

    async def _maybe_reclaim(self, now: float) -> None:
        if now - self._last_reclaim < max(5.0, self.lease / 4.0):
            return
        self._last_reclaim = now
        await self._reclaim(keys=[], args=[str(now), str(self.max_pages),
                                           self.prefix, "50"])

    async def remaining(self) -> int:
        pipe = self._client.pipeline()
        pipe.get(self.prefix + "pending")
        pipe.zcard(self.prefix + "claims_exp")
        pending_raw, inflight = await pipe.execute()
        pending = int(pending_raw) if pending_raw else 0
        self._cached_pending = max(0, pending)
        self._cached_inflight = int(inflight or 0)
        return self._cached_pending + self._cached_inflight

    @property
    def capped(self) -> bool:
        return self._capped

    @property
    def pending(self) -> int:
        return self._cached_pending

    @property
    def inflight_total(self) -> int:
        return self._cached_inflight
