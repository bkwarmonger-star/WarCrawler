from warcrawler.urlutil import (
    canonicalize, is_onion, get_registered_domain, resolve_links, ScopeFilter)


def test_canonicalize_strips_tracking_and_default_port():
    assert canonicalize("https://ex.com:443/a?utm_source=x&z=1#frag") == \
        "https://ex.com/a?z=1"


def test_canonicalize_rejects_non_http():
    assert canonicalize("mailto:a@b.com") is None
    assert canonicalize("javascript:void(0)") is None
    assert canonicalize("ftp://ex.com/x") is None


def test_canonicalize_empty_path_becomes_root():
    assert canonicalize("https://ex.com") == "https://ex.com/"


def test_is_onion():
    assert is_onion("http://abcdef.onion/path")
    assert not is_onion("https://example.com")


def test_registered_domain():
    assert get_registered_domain("a.b.example.com") == "example.com"
    assert get_registered_domain("shop.example.co.uk") == "example.co.uk"
    assert get_registered_domain("deadbeef.onion") == "deadbeef.onion"


def test_resolve_links_absolutizes_and_dedupes():
    links = resolve_links("https://ex.com/dir/", ["a", "a", "/b", "https://x.com/c"])
    assert "https://ex.com/dir/a" in links
    assert "https://ex.com/b" in links
    assert "https://x.com/c" in links
    assert len(links) == 3  # duplicate "a" collapsed


def test_scope_same_domain_and_deny():
    s = ScopeFilter(seeds=["https://ex.com/"], same_domain_only=True,
                    deny_domains=["bad.ex.com"])
    assert s.allowed("https://ex.com/page")
    assert s.allowed("https://sub.ex.com/page")      # same registered domain
    assert not s.allowed("https://other.com/page")   # different domain
    assert not s.allowed("https://bad.ex.com/page")  # denied


def test_scope_include_exclude_patterns():
    s = ScopeFilter(seeds=["https://ex.com/"], same_domain_only=False,
                    include_patterns=[r"/products/"], exclude_patterns=[r"\.pdf$"])
    assert s.allowed("https://ex.com/products/1")
    assert not s.allowed("https://ex.com/about")            # not included
    assert not s.allowed("https://ex.com/products/x.pdf")   # excluded
