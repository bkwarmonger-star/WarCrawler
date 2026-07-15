import asyncio

from warcrawler.storage import Storage
from warcrawler.report import collect, render_html, render_md


def _seed(tmp_path):
    async def go():
        st = Storage(tmp_path, "rep")
        await st.open()
        try:
            await st.save_page({
                "url": "https://ex.com/a", "final_url": "https://ex.com/a",
                "host": "ex.com", "depth": 0, "status": 200, "transport": "clearnet",
                "content_type": "text/html", "size": 100, "title": "Alpha",
                "lang": "en", "content_hash": "h1", "simhash": 1, "fetched_at": 10.0,
                "elapsed_ms": 5,
                "matches": [{"pattern": "breach", "count": 2, "samples": ["a breach here"]}],
                "extracted": {},
                "structured": {"summary": {"title": "Alpha", "author": "Jane",
                                           "published": "2026-02-01", "type": "article"}},
            }, content_text="a breach here")
            await st.save_page({
                "url": "https://ex.com/b", "final_url": "https://ex.com/b",
                "host": "ex.com", "depth": 1, "status": 200, "transport": "clearnet",
                "content_type": "text/html", "size": 50, "title": "Beta",
                "lang": "en", "content_hash": "h2", "simhash": 2, "fetched_at": 11.0,
                "elapsed_ms": 5, "matches": [], "extracted": {}, "structured": {},
            }, content_text="nothing")
            await st.add_alert("https://ex.com/a", "new",
                               [{"pattern": "breach", "count": 2}], "Alpha", "a breach here")
        finally:
            await st.close()
    asyncio.run(go())
    return str(tmp_path / "rep.sqlite")


def test_collect_counts(tmp_path):
    d = collect(_seed(tmp_path))
    assert d["pages"] == 2
    assert d["hosts"] == 1
    assert d["hit_count"] == 1
    assert d["alert_count"] == 1
    assert d["records"] and d["records"][0]["author"] == "Jane"
    assert ("ex.com", 2) in d["top_hosts"]


def test_render_html_contains_key_fields(tmp_path):
    d = collect(_seed(tmp_path))
    html = render_html("rep", d)
    assert "warcrawler report" in html
    assert "https://ex.com/a" in html
    assert "breach" in html
    assert "Watchlist hits" in html and "Recent alerts" in html


def test_render_md(tmp_path):
    md = render_md("rep", collect(_seed(tmp_path)))
    assert md.startswith("# warcrawler report")
    assert "https://ex.com/a" in md
