import asyncio

from warcrawler.config import FocusConfig, JobConfig
from warcrawler.focus import Scorer
from warcrawler.storage import Storage
from warcrawler.frontier import Frontier
from warcrawler.urlutil import ScopeFilter

try:
    from fakeredis import aioredis as fake_aioredis
    _HAS_FAKEREDIS = True
except Exception:
    _HAS_FAKEREDIS = False


def _run(coro):
    return asyncio.run(coro)


def _job():
    return JobConfig.from_dict({
        "name": "pf", "seeds": ["http://h.test/"], "transport": "clearnet",
        "throttle": {"profile": "aggressive"},
        "scope": {"same_domain_only": False, "max_depth": 9},
        "fleet": {"shared": True, "backend": "redis"},
    })


def _scope():
    return ScopeFilter(seeds=["http://h.test/"], same_domain_only=False)


def test_scorer_weights_anchor_and_url():
    s = Scorer(FocusConfig(enabled=True, keywords=["breach", "leak"],
                           anchor_weight=2.0, url_weight=1.0))
    assert s.active
    assert s.score("data breach report", "https://x/incident") == 2   # 1 anchor *2
    assert s.score("home", "https://x/leak/2024") == 1                # 1 url *1
    assert s.score("breach leak", "https://x/breach") == 5            # 2*2 + 1*1


def test_scorer_inactive_when_no_terms():
    assert Scorer(FocusConfig()).active is False
    assert Scorer(FocusConfig()).score("breach", "leak") == 0


def test_memory_frontier_crawls_highest_priority_first(tmp_path):
    async def go():
        st = Storage(tmp_path, "pf"); await st.open()
        f = Frontier(_job(), st, _scope()); await f.open()
        await f.add("http://h.test/low", 1, priority=1)
        await f.add("http://h.test/high", 1, priority=10)
        await f.add("http://h.test/mid", 1, priority=5)
        order = []
        while True:
            item = await f.next()
            if item is None:
                break
            order.append(item[0].rsplit("/", 1)[-1])
            await f.complete(item[2], item[0], "done")
        assert order == ["high", "mid", "low"]
        await st.close()
    _run(go())


def test_redis_frontier_priority_order(tmp_path):
    if not _HAS_FAKEREDIS:
        import pytest
        pytest.skip("fakeredis not installed")
    from warcrawler.redis_frontier import RedisFrontier

    async def go():
        st = Storage(tmp_path, "pfr"); await st.open()
        client = fake_aioredis.FakeRedis()
        f = RedisFrontier(_job(), st, _scope(), client=client)
        await f.open()
        try:
            await f.add("http://h.test/low", 1, priority=1)
            await f.add("http://h.test/high", 1, priority=10)
            await f.add("http://h.test/mid", 1, priority=5)
            order = []
            while True:
                item = await f.next()
                if item is None:
                    if await f.remaining() == 0:
                        break
                    continue
                order.append(item[0].rsplit("/", 1)[-1])
                await f.complete(item[2], item[0], "done")
            assert order == ["high", "mid", "low"]
        finally:
            await f.close(); await st.close()
    _run(go())
