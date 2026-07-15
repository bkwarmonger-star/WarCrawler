"""robots.txt handling with a hard on/off toggle.

When throttle.respect_robots is False this is bypassed entirely. When True we
fetch, cache and honour robots.txt (including crawl-delay) per host.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from .logutil import get_logger

log = get_logger()

# fetch_fn(url) -> (status_code, text) or None on failure
FetchFn = Callable[[str], Awaitable[Optional[Tuple[int, str]]]]


class RobotsCache:
    def __init__(self, enabled: bool, fetch_fn: FetchFn, user_agent: str = "*"):
        self.enabled = enabled
        self.fetch_fn = fetch_fn
        self.user_agent = user_agent
        self._cache: Dict[str, Optional[RobotFileParser]] = {}
        self._delays: Dict[str, Optional[float]] = {}

    def _root(self, url: str) -> str:
        p = urlsplit(url)
        return "{}://{}".format(p.scheme, p.netloc)

    async def _get_parser(self, url: str) -> Optional[RobotFileParser]:
        root = self._root(url)
        if root in self._cache:
            return self._cache[root]
        parser: Optional[RobotFileParser] = None
        try:
            result = await self.fetch_fn(root + "/robots.txt")
            if result is not None:
                status, text = result
                parser = RobotFileParser()
                if status == 200 and text:
                    parser.parse(text.splitlines())
                else:
                    # No robots or error -> allow all.
                    parser.parse([])
                try:
                    self._delays[root] = parser.crawl_delay(self.user_agent)
                except Exception:
                    self._delays[root] = None
        except Exception as exc:  # pragma: no cover
            log.debug("robots fetch failed for %s: %s", root, exc)
            parser = None
        self._cache[root] = parser
        return parser

    async def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        parser = await self._get_parser(url)
        if parser is None:
            return True  # fail-open: unreachable robots does not block crawling
        try:
            return parser.can_fetch(self.user_agent, url)
        except Exception:
            return True

    def crawl_delay(self, url: str) -> Optional[float]:
        if not self.enabled:
            return None
        return self._delays.get(self._root(url))
