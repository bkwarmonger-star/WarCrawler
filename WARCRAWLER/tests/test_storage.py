import asyncio

from warcrawler.storage import Storage


def _run(coro):
    return asyncio.run(coro)


def test_storage_roundtrip_and_big_simhash(tmp_path):
    async def go():
        st = Storage(tmp_path, "t")
        await st.open()
        try:
            # frontier dedup
            assert await st.add_frontier("https://ex.com/", "ex.com", 0) is True
            assert await st.add_frontier("https://ex.com/", "ex.com", 0) is False
            pending = await st.load_pending()
            assert ("https://ex.com/", 0) in pending

            # save a page with the maximum unsigned 64-bit simhash — this used
            # to overflow SQLite's signed INTEGER and abort the save.
            big = (1 << 64) - 1
            rec = {"url": "https://ex.com/", "final_url": "https://ex.com/",
                   "host": "ex.com", "depth": 0, "status": 200,
                   "transport": "clearnet", "content_type": "text/html",
                   "size": 10, "title": "Home", "lang": "en",
                   "content_hash": "abc123", "simhash": big,
                   "fetched_at": 1.0, "elapsed_ms": 5, "matches": [], "extracted": {}}
            await st.save_page(rec, content_text="strength has no age")

            assert await st.content_hash_exists("abc123") is True
            assert await st.count_pages() == 1
            assert (await st.load_simhashes())[0] == big  # preserved exactly

            hits = await st.search("strength")
            assert any(u == "https://ex.com/" for u, _ in hits)
        finally:
            await st.close()

    _run(go())


def test_conditional_get_validators_roundtrip(tmp_path):
    async def go():
        st = Storage(tmp_path, "cg")
        await st.open()
        try:
            assert await st.get_conditional("https://ex.com/") == (None, None)
            rec = {"url": "https://ex.com/", "final_url": "https://ex.com/",
                   "host": "ex.com", "depth": 0, "status": 200,
                   "transport": "clearnet", "content_type": "text/html",
                   "size": 5, "title": "H", "lang": None, "content_hash": "h",
                   "simhash": 1, "fetched_at": 1.0, "elapsed_ms": 1,
                   "etag": 'W/"abc"', "last_modified": "Mon, 01 Jan 2024 00:00:00 GMT",
                   "matches": [], "extracted": {}}
            await st.save_page(rec, content_text="hi")
            assert await st.get_conditional("https://ex.com/") == (
                'W/"abc"', "Mon, 01 Jan 2024 00:00:00 GMT")
        finally:
            await st.close()
    _run(go())


def test_mark_transitions(tmp_path):
    async def go():
        st = Storage(tmp_path, "t2")
        await st.open()
        try:
            await st.add_frontier("https://ex.com/a", "ex.com", 0)
            await st.mark("https://ex.com/a", "done")
            stats = await st.stats()
            assert stats["done"] == 1
            assert stats["pending"] == 0
        finally:
            await st.close()

    _run(go())
