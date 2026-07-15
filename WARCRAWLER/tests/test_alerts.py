import asyncio

from warcrawler.config import AlertConfig
from warcrawler.alerts import Alerter
from warcrawler.storage import Storage


def _run(coro):
    return asyncio.run(coro)


def test_should_alert_watchlist_only_on_new_or_changed_with_matches():
    a = Alerter(AlertConfig(enabled=True, triggers=["watchlist"]))
    m = [{"pattern": "x", "count": 1}]
    assert a.should_alert("new", m) is True
    assert a.should_alert("changed", m) is True
    assert a.should_alert("unchanged", m) is False   # never on unchanged
    assert a.should_alert("new", []) is False         # no matches -> no watchlist alert


def test_should_alert_new_and_changed_modes():
    a = Alerter(AlertConfig(enabled=True, triggers=["new", "changed"]))
    assert a.should_alert("new", []) is True          # any new page
    assert a.should_alert("changed", []) is True       # any changed page
    assert a.should_alert("unchanged", []) is False


def test_prior_hash_and_alert_store_roundtrip(tmp_path):
    async def go():
        st = Storage(tmp_path, "al")
        await st.open()
        try:
            assert await st.get_prior_hash("https://ex.com/") is None
            rec = {"url": "https://ex.com/", "final_url": "https://ex.com/",
                   "host": "ex.com", "depth": 0, "status": 200,
                   "transport": "clearnet", "content_type": "text/html",
                   "size": 1, "title": "T", "lang": None, "content_hash": "H1",
                   "simhash": 1, "fetched_at": 1.0, "elapsed_ms": 1,
                   "matches": [], "extracted": {}, "structured": {}}
            await st.save_page(rec, content_text="x")
            assert await st.get_prior_hash("https://ex.com/") == "H1"  # readable next pass

            await st.add_alert("https://ex.com/", "changed",
                               [{"pattern": "leak", "count": 2}], "T", "snippet")
            rows = await st.recent_alerts()
            assert len(rows) == 1 and rows[0][1] == "changed" and rows[0][2] == "https://ex.com/"
        finally:
            await st.close()
    _run(go())
