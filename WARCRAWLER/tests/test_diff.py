import asyncio

from warcrawler.diffutil import summarize_diff
from warcrawler.storage import Storage


def test_summarize_diff_counts_and_sample():
    old = "line one\nline two\nline three"
    new = "line one\nline two CHANGED\nline three\nbrand new leak entry"
    d = summarize_diff(old, new)
    assert d["added"] >= 2       # changed line + new line
    assert d["removed"] >= 1     # the replaced line
    assert "brand new leak entry" in d["added_sample"]


def test_summarize_diff_identical_is_empty():
    d = summarize_diff("same\ntext", "same\ntext")
    assert d["added"] == 0 and d["removed"] == 0


def test_storage_content_prior_and_changes(tmp_path):
    async def go():
        st = Storage(tmp_path, "d")
        await st.open()
        try:
            rec = {"url": "https://ex.com/", "final_url": "https://ex.com/",
                   "host": "ex.com", "depth": 0, "status": 200, "transport": "clearnet",
                   "content_type": "text/html", "size": 5, "title": "T", "lang": None,
                   "content_hash": "h1", "simhash": 1, "fetched_at": 1.0, "elapsed_ms": 1,
                   "matches": [], "extracted": {}, "structured": {}}
            await st.save_page(rec, content_text="version one", store_content=True)
            h, c = await st.get_prior("https://ex.com/")
            assert h == "h1" and c == "version one"   # content retained for diffing

            await st.add_change("https://ex.com/", 3, 1, "added leak line")
            rows = await st.recent_changes()
            assert len(rows) == 1 and rows[0][2] == 3 and "leak" in rows[0][4]
        finally:
            await st.close()
    asyncio.run(go())
