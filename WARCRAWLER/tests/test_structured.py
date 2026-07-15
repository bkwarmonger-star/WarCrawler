import lxml.html as LH

from warcrawler.structured import extract_structured
from warcrawler.config import ExtractConfig
from warcrawler.extract import Extractor

PAGE = """<html><head>
<title></title>
<meta property="og:title" content="OG Headline">
<meta property="og:site_name" content="Example News">
<meta property="og:type" content="article">
<meta property="og:locale" content="en_US">
<meta property="article:published_time" content="2026-01-02T10:00:00Z">
<meta name="description" content="A short summary.">
<meta name="twitter:image" content="https://ex.com/img.png">
<link rel="canonical" href="https://ex.com/canonical">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"NewsArticle","headline":"JSONLD Headline",
 "author":{"@type":"Person","name":"Jane Doe"},"datePublished":"2026-01-02T10:00:00Z"}
</script>
</head><body><p>hello</p></body></html>"""


def test_extract_structured_fields():
    s = extract_structured(LH.fromstring(PAGE))
    assert s["canonical"] == "https://ex.com/canonical"
    assert len(s["jsonld"]) == 1 and s["jsonld"][0]["@type"] == "NewsArticle"
    summ = s["summary"]
    assert summ["title"] == "OG Headline"          # og:title wins
    assert summ["author"] == "Jane Doe"            # from JSON-LD author object
    assert summ["published"].startswith("2026-01-02")
    assert summ["site_name"] == "Example News"
    assert summ["description"] == "A short summary."
    assert summ["image"] == "https://ex.com/img.png"
    assert summ["type"] == "article"


def test_jsonld_graph_flattening():
    html = ('<html><head><script type="application/ld+json">'
            '{"@graph":[{"@type":"Organization","name":"Acme"},'
            '{"@type":"WebSite","name":"Acme Site"}]}</script></head><body></body></html>')
    s = extract_structured(LH.fromstring(html))
    assert len(s["jsonld"]) == 2
    assert {o["name"] for o in s["jsonld"]} == {"Acme", "Acme Site"}


def test_malformed_jsonld_is_safe():
    html = ('<html><head><script type="application/ld+json">{bad,,,}</script>'
            '</head><body></body></html>')
    s = extract_structured(LH.fromstring(html))
    assert "jsonld" not in s  # nothing salvageable, but no crash


def test_extractor_uses_og_title_fallback_and_attaches_structured():
    rec, links, text = Extractor(ExtractConfig()).extract(
        "https://ex.com/", PAGE.encode("utf-8"), "text/html")
    assert rec["title"] == "OG Headline"          # empty <title> fell back to og:title
    assert rec["lang"] == "en"                    # from og:locale
    assert rec["structured"]["summary"]["author"] == "Jane Doe"
