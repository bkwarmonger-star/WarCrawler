from warcrawler.config import ExtractConfig
from warcrawler.extract import Extractor

HTML = b"""<!DOCTYPE html><html><head><title>Test Page</title></head>
<body>
  <h1>Main Heading</h1>
  <p>Contact code SECRET123 for the strength program.</p>
  <a href="/about">About</a>
  <a href="https://other.com/x">Ext</a>
  <a href="page2.html">Next</a>
</body></html>"""


def test_extract_title_links_watchlist_and_rules():
    cfg = ExtractConfig(watchlist=[r"SECRET\d+"], css_rules={"heading": "h1"})
    ex = Extractor(cfg)
    rec, links, text = ex.extract("https://ex.com/dir/", HTML, "text/html")

    assert rec["title"] == "Test Page"
    assert "https://ex.com/about" in links
    assert "https://ex.com/dir/page2.html" in links
    assert "https://other.com/x" in links

    assert rec["matches"] and rec["matches"][0]["pattern"] == r"SECRET\d+"
    assert rec["extracted"]["heading"] == ["Main Heading"]

    assert rec["content_hash"]
    assert 0 <= rec["simhash"] < (1 << 64)


def test_extract_handles_non_html_gracefully():
    ex = Extractor(ExtractConfig())
    rec, links, text = ex.extract("https://ex.com/x.bin", b"\x00\x01\x02", "application/octet-stream")
    assert links == [] or isinstance(links, list)
    assert rec["content_hash"]
