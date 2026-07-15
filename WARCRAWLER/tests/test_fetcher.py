"""Tests for the Tor transport's circuit isolation and rotation logic.

No live Tor or network is used: client construction is offline (httpx-socks
builds a transport lazily), and the rotation loop is driven with fakes.
"""
import asyncio

from warcrawler.config import JobConfig
from warcrawler.fetcher import Fetcher, FetchResult, parse_retry_after
from warcrawler.stealth import Stealth


def test_parse_retry_after_seconds_and_date_and_junk():
    assert parse_retry_after("120") == 120.0
    # HTTP-date 60s in the future → ~60 (allow slack)
    import time
    from email.utils import formatdate
    secs = parse_retry_after(formatdate(time.time() + 60, usegmt=True), now=time.time())
    assert 55 <= secs <= 65
    assert parse_retry_after(None) is None
    assert parse_retry_after("not-a-date") is None


def _tor_job(**throttle):
    return JobConfig.from_dict({
        "name": "t", "seeds": ["http://x.onion/"], "transport": "tor",
        "tor": {"enabled": True, "rotate_on_block": True, "num_circuits": 4},
        "throttle": dict({"max_retries": 3}, **throttle),
    })


def test_tor_client_cached_distinct_and_droppable():
    job = _tor_job()
    f = Fetcher(job, Stealth(job.stealth))
    a, a2 = f._tor_client_for("w0"), f._tor_client_for("w0")
    assert a is a2                              # same label -> cached, reused
    b = f._tor_client_for("r0s1")
    assert b is not a                           # distinct label -> distinct client
    asyncio.run(f.drop_tor_client("r0s1"))
    assert "tor:r0s1" not in f._clients         # dropped
    c = f._tor_client_for("r0s1")
    assert c is not b                           # rebuilt fresh after drop
    asyncio.run(f.aclose())


def test_rotation_uses_fresh_identity_each_time_and_drops(tmp_path):
    from warcrawler.engine import Engine

    seen = []

    class FakeFetcher:
        def __init__(self):
            self.dropped = []

        async def fetch(self, url, tor_circuit="w0", extra_headers=None):
            seen.append(tor_circuit)
            return FetchResult(url=url, status=429, blocked=True)  # always blocked

        async def drop_tor_client(self, label):
            self.dropped.append(label)

    class FakeTor:
        def __init__(self):
            self.rotations = 0

        async def new_circuit(self):
            self.rotations += 1

    # no real waiting between retries
    async def _no_sleep(*a, **k):
        return None
    orig_sleep = asyncio.sleep
    asyncio.sleep = _no_sleep

    async def go():
        eng = Engine(_tor_job(), tmp_path)   # construct inside a running loop
        eng.fetcher, eng.tor = FakeFetcher(), FakeTor()
        res = await eng._fetch_with_retries("http://x.onion/p", wid=0)
        return eng, res

    try:
        eng, res = asyncio.run(go())
    finally:
        asyncio.sleep = orig_sleep

    # 3 attempts: default slot, then two brand-new identities
    assert seen == ["w0", "r0s1", "r0s2"]
    assert len(set(seen)) == 3                      # every rotation is unique
    assert eng.tor.rotations == 2                   # rotated before each retry
    assert set(eng.fetcher.dropped) == {"r0s1", "r0s2"}   # stale circuits released
    assert res.status == 429                        # returns the last response
