"""Programmatic login: optionally scrape a CSRF token, submit credentials, and
capture the resulting session cookies for the crawl.

Handles the common cases (simple form POST, and CSRF-protected forms that need
a GET-then-POST). Cookies are returned as a dict to inject into every request.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

from .config import LoginConfig
from .logutil import get_logger

log = get_logger()


def _build_client(transport: str, tor_socks_port: int, proxy: Optional[str], timeout: float):
    import httpx
    kw = dict(follow_redirects=True, timeout=timeout)
    if transport == "tor":
        from httpx_socks import AsyncProxyTransport
        t = AsyncProxyTransport.from_url(
            "socks5://wclogin:wclogin@127.0.0.1:{}".format(tor_socks_port), rdns=True)
        return httpx.AsyncClient(transport=t, **kw)
    if proxy:
        try:
            return httpx.AsyncClient(proxy=proxy, **kw)
        except TypeError:
            return httpx.AsyncClient(proxies=proxy, **kw)
    return httpx.AsyncClient(**kw)


def extract_token(html: str, cfg: LoginConfig) -> Optional[str]:
    """Pull a CSRF token from a login page via regex or hidden-input name."""
    if cfg.csrf_regex:
        m = re.search(cfg.csrf_regex, html or "")
        if m:
            return m.group(1) if m.groups() else m.group(0)
    if cfg.csrf_field:
        try:
            import lxml.html as LH
            vals = LH.fromstring(html).xpath("//input[@name=$n]/@value", n=cfg.csrf_field)
            if vals:
                return vals[0]
        except Exception:
            pass
        f = re.escape(cfg.csrf_field)
        for pat in (r'<input[^>]+name=["\']' + f + r'["\'][^>]+value=["\']([^"\']*)',
                    r'<input[^>]+value=["\']([^"\']*)["\'][^>]+name=["\']' + f + r'["\']'):
            m = re.search(pat, html or "", re.I)
            if m:
                return m.group(1)
    return None


async def perform_login(cfg: LoginConfig, transport: str = "clearnet",
                        tor_socks_port: int = 9050, proxy: Optional[str] = None,
                        timeout: float = 30.0) -> Dict[str, object]:
    """Return {ok, status, cookies} after attempting login."""
    client = _build_client(transport, tor_socks_port, proxy, timeout)
    try:
        data = dict(cfg.data)
        if cfg.csrf_url:
            r = await client.get(cfg.csrf_url)
            token = extract_token(r.text, cfg)
            if token:
                data[cfg.csrf_post_field or cfg.csrf_field or "csrf_token"] = token
        method = (cfg.method or "POST").upper()
        if method == "GET":
            resp = await client.get(cfg.url, params=data or None)
        elif cfg.json_body:
            resp = await client.post(cfg.url, json=data)
        else:
            resp = await client.post(cfg.url, data=data)
        ok = True
        if cfg.success_contains:
            ok = cfg.success_contains in (resp.text or "")
        cookies = {k: v for k, v in client.cookies.items()}
        return {"ok": ok, "status": resp.status_code, "cookies": cookies}
    except Exception as exc:
        log.warning("login failed: %s", exc)
        return {"ok": False, "status": 0, "cookies": {}}
    finally:
        await client.aclose()
