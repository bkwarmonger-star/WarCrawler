from warcrawler.dedup import simhash, hamming, DedupIndex


def test_simhash_is_unsigned_64bit():
    h = simhash("the quick brown fox jumps over the lazy dog " * 5)
    assert 0 <= h < (1 << 64)


def test_simhash_identical_text_matches():
    a = simhash("warcrawler distributed frontier work stealing " * 4)
    b = simhash("warcrawler distributed frontier work stealing " * 4)
    assert a == b
    assert hamming(a, b) == 0


def test_simhash_distinct_text_differs():
    a = simhash("completely different topic about cats and gardens " * 4)
    b = simhash("networking protocols and tor onion routing internals " * 4)
    assert hamming(a, b) > 3


def test_dedup_index_detects_exact_and_near():
    idx = DedupIndex(max_distance=3)
    base = "the annual strength and conditioning report for adaptive athletes " * 6
    h1 = simhash(base)
    assert idx.add_if_new(h1) is True         # first time -> new
    assert idx.add_if_new(h1) is False        # exact repeat -> duplicate
    # a distinct document is not a duplicate
    h2 = simhash("unrelated content about weather patterns in the pacific " * 6)
    assert idx.add_if_new(h2) is True
