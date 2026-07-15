from warcrawler.config import StealthConfig
from warcrawler.stealth import Stealth


def test_cookies_are_assembled_into_cookie_header():
    cfg = StealthConfig(rotate_user_agent=False, user_agents=["UA/1"],
                        cookies={"session": "abc", "csrf": "xyz"})
    h = Stealth(cfg).headers()
    assert h["Cookie"] == "session=abc; csrf=xyz"
    assert h["User-Agent"] == "UA/1"


def test_static_headers_are_sent_and_win():
    cfg = StealthConfig(
        header_profiles=[{"Accept-Language": "en-US"}],
        cookies={"s": "fromcookie"},
        headers={"Authorization": "Bearer TOKEN",
                 "Cookie": "session=override",   # explicit header beats cookies map
                 "Accept-Language": "fr-FR"})    # and beats the rotating profile
    h = Stealth(cfg).headers()
    assert h["Authorization"] == "Bearer TOKEN"
    assert h["Cookie"] == "session=override"
    assert h["Accept-Language"] == "fr-FR"


def test_pinned_user_agent_stays_fixed():
    # Pin the UA so a session cookie isn't invalidated by a rotating fingerprint.
    cfg = StealthConfig(user_agents=["A", "B", "C"])
    s = Stealth(cfg, pinned_user_agent="PINNED/9")
    assert s.headers()["User-Agent"] == "PINNED/9"
    assert s.headers()["User-Agent"] == "PINNED/9"
