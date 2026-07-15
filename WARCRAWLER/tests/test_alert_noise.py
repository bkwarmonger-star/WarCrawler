import asyncio

from warcrawler.config import AlertConfig
from warcrawler.alerts import Alerter
from warcrawler.storage import Storage


def test_digest_buffers_then_flushes():
    async def go():
        a = Alerter(AlertConfig(enabled=True, digest=True))  # no webhook -> _post no-op
        await a.deliver({"url": "u1"})
        await a.deliver({"url": "u2"})
        assert len(a._buffer) == 2          # buffered, not sent per-alert
        await a.flush("job")
        assert a._buffer == []              # flushed
    asyncio.run(go())


def test_non_digest_does_not_buffer():
    async def go():
        a = Alerter(AlertConfig(enabled=True, digest=False))
        await a.deliver({"url": "u1"})
        assert a._buffer == []              # delivered immediately (no-op without webhook)
    asyncio.run(go())


def test_last_alert_time_supports_cooldown(tmp_path):
    async def go():
        st = Storage(tmp_path, "cd")
        await st.open()
        try:
            assert await st.last_alert_time("https://ex.com/") is None
            await st.add_alert("https://ex.com/", "changed", [], "T", "s")
            t = await st.last_alert_time("https://ex.com/")
            assert t is not None and t > 0
        finally:
            await st.close()
    asyncio.run(go())
