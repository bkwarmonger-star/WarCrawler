"""Alerting: decide when a page is worth alerting on, and deliver via webhook.

Change classification (new / changed / unchanged) is computed by the engine
from the URL's prior content hash; this module decides whether that, combined
with watchlist matches, should fire — and posts a JSON payload to a webhook.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .config import AlertConfig
from .logutil import get_logger

log = get_logger()


class Alerter:
    def __init__(self, cfg: AlertConfig):
        self.cfg = cfg
        self._client = None
        self._buffer: List[Dict[str, Any]] = []   # for digest mode

    def should_alert(self, change: str, matches: List[Any]) -> bool:
        """change is one of new|changed|unchanged."""
        if change == "unchanged":
            return False
        triggers = self.cfg.triggers
        if "watchlist" in triggers and matches and change in ("new", "changed"):
            return True
        if "new" in triggers and change == "new":
            return True
        if "changed" in triggers and change == "changed":
            return True
        return False

    async def deliver(self, payload: Dict[str, Any]) -> None:
        """Buffer for a digest, or POST immediately."""
        if self.cfg.digest:
            self._buffer.append(payload)
        else:
            await self._post(payload)

    async def flush(self, job: str = "") -> None:
        """Send buffered alerts as one digest POST (no-op if empty)."""
        if not self._buffer:
            return
        await self._post({"job": job, "digest": True,
                          "count": len(self._buffer), "alerts": self._buffer})
        self._buffer = []

    async def _post(self, payload: Dict[str, Any]) -> None:
        if not self.cfg.webhook_url:
            return
        try:
            import httpx
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=self.cfg.webhook_timeout)
            await self._client.post(self.cfg.webhook_url, json=payload)
        except Exception as exc:  # best-effort; never let an alert kill a crawl
            log.debug("webhook post failed: %s", exc)

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
