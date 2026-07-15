from warcrawler.feeds import parse_feed, feeds_from_html

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Site</title><link>https://ex.com/</link>
  <item><title>Breach disclosed</title><link>https://ex.com/a</link>
        <pubDate>Mon, 02 Feb 2026 10:00:00 GMT</pubDate>
        <description>A breach.</description><guid>a1</guid></item>
  <item><title>Second</title><link>https://ex.com/b</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Site</title>
  <entry><title>Post One</title>
    <link rel="alternate" href="https://ex.com/p1"/>
    <published>2026-02-01T00:00:00Z</published>
    <summary>hello</summary><id>urn:1</id></entry>
  <entry><title>Post Two</title><link href="https://ex.com/p2"/></entry>
</feed>"""


def test_parse_rss():
    items = parse_feed(RSS)
    assert [i["link"] for i in items] == ["https://ex.com/a", "https://ex.com/b"]
    assert items[0]["title"] == "Breach disclosed"
    assert items[0]["published"].startswith("Mon, 02 Feb")


def test_parse_atom():
    items = parse_feed(ATOM)
    assert [i["link"] for i in items] == ["https://ex.com/p1", "https://ex.com/p2"]
    assert items[0]["title"] == "Post One"
    assert items[0]["published"].startswith("2026-02-01")


def test_parse_bad_feed_is_safe():
    assert parse_feed(b"not a feed <<<") == []


def test_feeds_from_html_autodiscovery():
    html = (b'<html><head>'
            b'<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
            b'<link rel="stylesheet" href="/x.css">'
            b'</head><body></body></html>')
    found = feeds_from_html(html, "https://ex.com/blog/")
    assert found == ["https://ex.com/feed.xml"]
