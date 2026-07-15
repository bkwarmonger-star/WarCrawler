import asyncio
import collections

from warcrawler.config import JobConfig
from warcrawler.storage import Storage
from warcrawler.frontier import Frontier
from warcrawler.sqlite_frontier import SQLiteFrontier
from warcrawler.urlutil import ScopeFilter


def _run(coro):
    return asyncio.run(coro)


def _job(max_pages, shared=False):
    return JobConfig.from_dict({
        "name": "ftest", "seeds": ["http://h.test/"], "transport": "clearnet",
        "throttle": {"profile": "aggressive"},        # delay 0, high concurrency
        "scope": {"same_domain_only": False, "max_depth": 9, "max_pages": max_pages},
        "fleet": {"shared": shared},
    })


def _scope():
    return ScopeFilter(seeds=["http://h.test/"], same_domain_only=False)


def test_memory_frontier_hard_cap(tmp_path):
    async def go():
        st = Storage(tmp_path, "mem"); await st.open()
        f = Frontier(_job(5), st, _scope()); await f.open()
        for i in range(20):
            await f.add("http://h.test/p{}".format(i), 1)
        got = 0
        while True:
            item = await f.next()
            if item is None:
                break
            got += 1
            await f.complete(item[2], item[0], "done")
        assert got == 5           # never dispatched more than the cap
        assert f.capped is True
        await st.close()
    _run(go())


def test_shared_claim_uniqueness(tmp_path):
    async def go():
        st = Storage(tmp_path, "shared"); await st.open()
        f = SQLiteFrontier(_job(0, shared=True), st, _scope()); await f.open()
        urls = ["http://h{}.test/p{}".format(h, i) for h in range(3) for i in range(10)]
        for u in urls:
            await f.add(u, 1)
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
        assert max(claimed.values()) == 1          # nothing claimed twice
        assert await f.remaining() == 0
        await f.close(); await st.close()
    _run(go())


def test_shared_frontier_hard_cap(tmp_path):
    async def go():
        st = Storage(tmp_path, "sharedcap"); await st.open()
        f = SQLiteFrontier(_job(10, shared=True), st, _scope()); await f.open()
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
        assert done == 10          # exact hard cap, no overshoot
        assert f.capped is True
        await st.close()
    _run(go())
