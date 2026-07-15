"""Stealth / anti-blocking: user-agent, header and proxy rotation."""
from __future__ import annotations

import itertools
import random
from typing import Dict, List, Optional

from .config import StealthConfig


class Stealth:
    def __init__(self, cfg: StealthConfig, pinned_user_agent: Optional[str] = None):
        self.cfg = cfg
        self.pinned_ua = pinned_user_agent
        self._ua_cycle = itertools.cycle(cfg.user_agents or ["warcrawler/1.0"])
        self._proxy_cycle = itertools.cycle(cfg.proxies) if cfg.proxies else None
        self._sticky_proxy: Dict[str, str] = {}

    def user_agent(self) -> str:
        if self.pinned_ua:
            return self.pinned_ua
        if not self.cfg.rotate_user_agent:
            return (self.cfg.user_agents or ["warcrawler/1.0"])[0]
        return next(self._ua_cycle)

    def headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self.cfg.header_profiles:
            h.update(random.choice(self.cfg.header_profiles))
        h["User-Agent"] = self.user_agent()
        # Session cookies (convenience map) then static headers, which win —
        # so an explicit Cookie/Authorization header always takes precedence.
        if self.cfg.cookies:
            h["Cookie"] = "; ".join("{}={}".format(k, v)
                                    for k, v in self.cfg.cookies.items())
        if self.cfg.headers:
            h.update(self.cfg.headers)
        return h

    def proxy_for(self, host: str) -> Optional[str]:
        if not self.cfg.proxies:
            return None
        mode = self.cfg.proxy_rotation
        if mode == "random":
            return random.choice(self.cfg.proxies)
        if mode == "sticky_host":
            if host not in self._sticky_proxy:
                self._sticky_proxy[host] = random.choice(self.cfg.proxies)
            return self._sticky_proxy[host]
        # round_robin
        return next(self._proxy_cycle)  # type: ignore[arg-type]

    @property
    def proxies(self) -> List[str]:
        return list(self.cfg.proxies)
