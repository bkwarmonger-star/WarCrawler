"""Tests for the Redis frontier.

These run against an in-process fakeredis (with Lua support) if it is
installed; otherwise they skip. On a real machine, verify with:
    warcrawler redis-test --redis-url redis://localhost:6379/0
"""
import asyncio
import collections

import pytest

from warcrawler.config import JobConfig
from warcrawler.storage import Storage
from warcrawler.urlutil import ScopeFilter

try:
    from fakeredis import aioredis as fake_aioredis
    _HAS_FAKEREDIS = True
except Exception:
    _HAS_FAKEREDIS = False

pytestmark = pytest.mark.skipif(not _HAS_FAKEREDIS,
                                reason="fakeredis (with lua) not installed")


def _run(coro):
    return asyncio.run(coro)


def _job(max_pages):
    return JobConfig.from_dict({
        "name": "rtest", "seeds": ["http://h.test/"], "transport": "clearnet",
        "throttle": {"profile": "aggressive"},
        "scope": {"same_domain_only": False, "max_depth": 9, "max_pages": max_pages},
        "fleet": {"shared": True, "backend": "redis"},
    })


def _make(job, tmp_path):
    from warcrawler.redis_frontier import RedisFrontier
    st = Storage(tmp_path, "rtest")
    client = fake_aioredis.FakeRedis()
    scope = ScopeFilter(seeds=["http://h.test/"], same_domain_only=False)
    return st, RedisFrontier(job, st, scope, client=client)


def test_redis_claim_uniqueness(tmp_path):
    async def go():
        st, f = _make(_job(0), tmp_path)
        await st.open(); await f.open()
        try:
            urls = ["http://h{}.test/p{}".format(h, i) for h in range(3) for i in range(10)]
            for u in urls:
                assert await f.add(u, 1) is True
            assert await f.add(urls[0], 1) is False   # global dedup
            claimed = collections.Counter()

            async def claimer():
                while True:
                    item = await f.next()
                    if item is None:
                        if await f.remaining() == 0:
                            return
                        await asyncio.sleep(0.003)
                        continue
                    claimed[item[0]] += 1
                    await f.complete(item[2], item[0], "done")

            await asyncio.gather(*[claimer() for _ in range(6)])
            assert len(claimed) == len(urls)
            assert max(claimed.values()) == 1
            assert await f.remaining() == 0
        finally:
            await f.close(); await st.close()
    _run(go())


def test_redis_hard_cap(tmp_path):
    async def go():
        st, f = _make(_job(10), tmp_path)
        await st.open(); await f.open()
        try:
            for i in range(40):
                await f.add("http://h.test/p{}".format(i), 1)
            done = 0
            async def claimer():
                nonlocal done
                while True:
                    item = await f.next()
                    if item is None:
                        if f.capped or await f.remaining() == 0:
                            return
                        await asyncio.sleep(0.003)
                        continue
                    done += 1
                    await f.complete(item[2], item[0], "done")
            await asyncio.gather(*[claimer() for _ in range(4)])
            assert done == 10
            assert f.capped is True
        finally:
            await f.close(); await st.close()
    _run(go())
