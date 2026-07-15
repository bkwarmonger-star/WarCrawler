import asyncio
import gzip

from warcrawler.sitemap import (
    parse_sitemap, sitemaps_from_robots, default_sitemap, gather_urls)

URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://ex.com/a</loc></url>
  <url><loc>https://ex.com/b</loc></url>
</urlset>"""

INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://ex.com/sitemap-1.xml</loc></sitemap>
  <sitemap><loc>https://ex.com/sitemap-2.xml</loc></sitemap>
</sitemapindex>"""


def test_parse_urlset_returns_pages():
    pages, subs = parse_sitemap(URLSET)
    assert pages == ["https://ex.com/a", "https://ex.com/b"]
    assert subs == []


def test_parse_index_returns_subsitemaps():
    pages, subs = parse_sitemap(INDEX)
    assert pages == []
    assert subs == ["https://ex.com/sitemap-1.xml", "https://ex.com/sitemap-2.xml"]


def test_parse_handles_gzip():
    pages, subs = parse_sitemap(gzip.compress(URLSET))
    assert pages == ["https://ex.com/a", "https://ex.com/b"]


def test_parse_bad_xml_is_safe():
    assert parse_sitemap(b"not xml <<<") == ([], [])


def test_sitemaps_from_robots():
    robots = "User-agent: *\nDisallow: /x\nSitemap: https://ex.com/sm.xml\n"
    assert sitemaps_from_robots(robots) == ["https://ex.com/sm.xml"]
    assert default_sitemap("https://ex.com/path?q=1") == "https://ex.com/sitemap.xml"


def test_gather_urls_follows_index():
    store = {
        "https://ex.com/sitemap.xml": INDEX,
        "https://ex.com/sitemap-1.xml": URLSET,
        "https://ex.com/sitemap-2.xml":
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            b'<url><loc>https://ex.com/c</loc></url></urlset>',
    }

    async def fetch_bytes(u):
        return store.get(u)

    async def go():
        return await gather_urls(fetch_bytes, ["https://ex.com/sitemap.xml"], max_urls=100)

    urls = asyncio.run(go())
    assert set(urls) == {"https://ex.com/a", "https://ex.com/b", "https://ex.com/c"}
