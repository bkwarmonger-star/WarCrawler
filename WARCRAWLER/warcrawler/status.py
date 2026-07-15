"""Live status dashboard. Uses rich when attached to a TTY, else logs lines."""
from __future__ import annotations

import asyncio
import os
import sys
import time

from .logutil import get_logger

log = get_logger()

try:
    from rich.live import Live
    from rich.table import Table
    from rich.console import Console
    _HAS_RICH = True
except Exception:  # pragma: no cover
    _HAS_RICH = False


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return "{:.1f}{}".format(n, unit)
        n /= 1024.0
    return "{:.1f}PB".format(n)


class StatusReporter:
    def __init__(self, engine, interval: float = 0.5):
        self.engine = engine
        self.interval = interval

    def _render_table(self):
        s = self.engine.stats
        f = self.engine.frontier
        t = Table(title="warcrawler — {}".format(self.engine.cfg.name),
                  expand=True)
        t.add_column("metric", style="bold")
        t.add_column("value", justify="right")
        elapsed = time.time() - s.start
        t.add_row("elapsed", "{:.0f}s".format(elapsed))
        t.add_row("pages fetched", str(s.pages))
        t.add_row("queue pending", str(f.pending))
        t.add_row("in-flight", str(f.inflight_total))
        t.add_row("errors", str(s.errors))
        t.add_row("robots-skipped", str(s.skipped))
        t.add_row("duplicates", str(s.dupes))
        t.add_row("unchanged (304)", str(s.unchanged))
        t.add_row("watchlist hits", str(s.matches))
        t.add_row("changed pages", str(s.changed))
        t.add_row("alerts fired", str(s.alerts))
        t.add_row("downloaded", _human_bytes(s.bytes))
        t.add_row("rate", "{:.1f} pages/s".format(s.rate()))
        return t

    async def run(self, stop_event: asyncio.Event) -> None:
        await self.run_until(stop_event)

    async def run_until(self, *events) -> None:
        def done() -> bool:
            return any(e.is_set() for e in events)

        # Child processes force plain logging so several dashboards don't fight
        # over one terminal.
        use_rich = (_HAS_RICH and sys.stderr.isatty()
                    and not os.environ.get("WARCRAWLER_PLAIN_STATUS"))
        if use_rich:
            console = Console(stderr=True)
            with Live(self._render_table(), console=console,
                      refresh_per_second=4, transient=False) as live:
                while not done():
                    live.update(self._render_table())
                    await asyncio.sleep(self.interval)
                live.update(self._render_table())
        else:
            last = 0.0
            while not done():
                now = time.time()
                if now - last >= 5.0:
                    s = self.engine.stats
                    f = self.engine.frontier
                    log.info("pages=%d pending=%d inflight=%d err=%d dup=%d hits=%d %.1f p/s",
                             s.pages, f.pending, f.inflight_total, s.errors,
                             s.dupes, s.matches, s.rate())
                    last = now
                await asyncio.sleep(1.0)
