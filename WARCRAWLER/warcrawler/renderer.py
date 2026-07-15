"""Optional JavaScript rendering via Playwright (Chromium).

Heavy and only semi-portable (needs a browser binary), so it is fully
optional and import-guarded. Enable with render.enabled in a job file after
running: pip install playwright && playwright install chromium
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import JobConfig
from .logutil import get_logger

log = get_logger()

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
window.chrome = window.chrome || {runtime: {}};
"""


class Renderer:
    def __init__(self, cfg: JobConfig):
        self.cfg = cfg
        self._pw = None
        self._browser = None
        self._context = None

    async def start(self) -> None:
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        proxy = None
        if self.cfg.transport == "tor" or (self.cfg.tor.enabled and self.cfg.transport != "clearnet"):
            proxy = {"server": "socks5://127.0.0.1:{}".format(self.cfg.tor.socks_port)}
        self._browser = await self._pw.chromium.launch(headless=True)
        ua = self.cfg.user_agent or (self.cfg.stealth.user_agents or [None])[0]
        self._context = await self._browser.new_context(
            user_agent=ua, proxy=proxy, ignore_https_errors=True)
        if self.cfg.render.stealth:
            await self._context.add_init_script(_STEALTH_JS)

    async def render(self, url: str) -> Optional[str]:
        if self._context is None:
            await self.start()
        page = await self._context.new_page()
        try:
            await page.goto(url, wait_until=self.cfg.render.wait_until,
                            timeout=int(self.cfg.render.timeout * 1000))
            if self.cfg.render.wait_ms:
                await page.wait_for_timeout(self.cfg.render.wait_ms)
            return await page.content()
        finally:
            await page.close()

    async def stop(self) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                try:
                    await closer.close()
                except Exception:
                    pass
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass


def create_renderer(cfg: JobConfig) -> Optional[Renderer]:
    if not cfg.render.enabled:
        return None
    try:
        import playwright  # noqa: F401
    except Exception:
        log.warning("render.enabled but playwright is not installed; "
                    "run: pip install playwright && playwright install chromium")
        return None
    return Renderer(cfg)
