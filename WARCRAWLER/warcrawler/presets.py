"""Static presets: user-agents, header profiles, and throttle profiles.

These are plain data so they work identically on every OS and require no
third-party dependencies.
"""
from __future__ import annotations

from typing import Dict, List

# A rotating pool of realistic desktop/mobile user agents. Extend freely in a
# job file via stealth.user_agents.
USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    # A neutral crawler UA for sites that prefer an honest identity.
    "warcrawler/1.0 (+https://example.invalid/warcrawler)",
]

# Base header profiles applied alongside the rotating UA. The engine fills in
# User-Agent; these add the surrounding fingerprint.
HEADER_PROFILES: List[Dict[str, str]] = [
    {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    },
    {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "DNT": "1",
    },
]

# Named throttle presets. A job selects one via throttle.profile and may
# override any individual field.
#   per_host_delay        seconds to wait between requests to the same host
#   per_host_concurrency  simultaneous in-flight requests per host
#   global_concurrency    total simultaneous in-flight requests (the fleet size)
#   respect_robots        obey robots.txt when True
#   adaptive_backoff      slow/rotate only when a target starts blocking
THROTTLE_PRESETS: Dict[str, Dict[str, object]] = {
    "polite": {
        "per_host_delay": 2.0,
        "per_host_concurrency": 1,
        "global_concurrency": 8,
        "respect_robots": True,
        "adaptive_backoff": True,
    },
    "normal": {
        "per_host_delay": 0.5,
        "per_host_concurrency": 2,
        "global_concurrency": 16,
        "respect_robots": True,
        "adaptive_backoff": True,
    },
    "aggressive": {
        "per_host_delay": 0.0,
        "per_host_concurrency": 8,
        "global_concurrency": 64,
        "respect_robots": False,
        "adaptive_backoff": True,
    },
    "stealth": {
        "per_host_delay": 5.0,
        "per_host_concurrency": 1,
        "global_concurrency": 6,
        "respect_robots": False,
        "adaptive_backoff": True,
    },
    # No limits at all. This can get you banned or, over Tor, flagged. Provided
    # because you asked for it; adaptive_backoff is still off here.
    "unlimited": {
        "per_host_delay": 0.0,
        "per_host_concurrency": 64,
        "global_concurrency": 256,
        "respect_robots": False,
        "adaptive_backoff": False,
    },
}

DEFAULT_PROFILE = "normal"
