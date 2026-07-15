"""Transport layer.

Backends, chosen per request:
  * clearnet   -> httpx.AsyncClient (HTTP/2, connection pooling)
  * tls-stealth-> curl_cffi AsyncSession impersonating a real browser TLS/JA3
  * tor        -> httpx over a SOCKS5 proxy (httpx-socks) with remote DNS so
                  .onion resolves inside Tor; per-worker SOCKS auth yields
                  isolated circuits (Tor IsolateSOCKSAuth).

Only the clearnet backend is exercised in the build sandbox (HTTP/HTTPS only);
the Tor and curl_cffi paths are import-guarded and run on your machine.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from .config import JobConfig
from .logutil import get_logger
from .stealth import Stealth
from .urlutil import is_onion, get_host

log = get_logger()

try:
    import httpx
    _HAS_HTTPX = True
except Exception:  # pragma: no cover
    _HAS_HTTPX = False


def parse_retry_after(value: Optional[str], now: Optional[float] = None) -> Optional[float]:
    """Parse a Retry-After header (delta-seconds or HTTP-date) into seconds."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        from email.utils import parsedate_to_datetime
        import time as _t
        dt = parsedate_to_datetime(value)
        if dt is None:
            return None
        target = dt.timestamp()
        return max(0.0, target - (now if now is not None else _t.time()))
    except Exception:
        return None


@dataclass
class FetchResult:
    url: str
    final_url: str = ""
    status: int = 0
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    content_type: str = ""
    elapsed_ms: int = 0
    transport: str = "clearnet"
    error: Optional[str] = None
    blocked: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 400


def _make_httpx_client(cfg: JobConfig, proxy: Optional[str] = None):
    kwargs = dict(
        follow_redirects=True,
        timeout=cfg.throttle.timeout,
        max_redirects=cfg.stealth.max_redirects,
        headers={},
        verify=True,
    )
    # httpx renamed proxies->proxy across versions; support both.
    try:
        if proxy:
            return httpx.AsyncClient(proxy=proxy, **kwargs)
        return httpx.AsyncClient(**kwargs)
    except TypeError:
        if proxy:
            return httpx.AsyncClient(proxies=proxy, **kwargs)
        return httpx.AsyncClient(**kwargs)


def _make_tor_client(cfg: JobConfig, label: str):
    """httpx client routed through Tor.

    The SOCKS username/password is set to `label`. Under Tor's IsolateSOCKSAuth,
    distinct labels ride distinct circuits, and a never-before-seen label always
    yields a brand-new circuit — which is how rotation gets a fresh path
    regardless of num_circuits.
    """
    from httpx_socks import AsyncProxyTransport
    proxy_url = "socks5://{}:{}@127.0.0.1:{}".format(label, label, cfg.tor.socks_port)
    transport = AsyncProxyTransport.from_url(proxy_url, rdns=True)
    return httpx.AsyncClient(transport=transport, follow_redirects=True,
                             timeout=cfg.throttle.timeout,
                             max_redirects=cfg.stealth.max_redirects, verify=True)


class Fetcher:
    def __init__(self, cfg: JobConfig, stealth: Stealth):
        if not _HAS_HTTPX:
            raise RuntimeError("httpx is required. pip install 'httpx[socks]'")
        self.cfg = cfg
        self.stealth = stealth
        self._clients: Dict[str, "httpx.AsyncClient"] = {}
        self._cffi_sessions: Dict[str, object] = {}

    # ---- backend resolution ---------------------------------------------
    def _resolve_transport(self, url: str) -> str:
        if self.cfg.transport == "tor":
            return "tor"
        if self.cfg.transport == "clearnet":
            return "clearnet"
        # auto
        if is_onion(url):
            return "tor"
        return "clearnet"

    def _client_for(self, proxy: Optional[str]):
        key = "proxy:" + proxy if proxy else "direct"
        if key not in self._clients:
            self._clients[key] = _make_httpx_client(self.cfg, proxy)
        return self._clients[key]

    def _tor_client_for(self, label: str):
        key = "tor:{}".format(label)
        if key not in self._clients:
            self._clients[key] = _make_tor_client(self.cfg, label)
        return self._clients[key]

    async def drop_tor_client(self, label: str) -> None:
        """Close and forget a Tor client so its circuit is never reused."""
        client = self._clients.pop("tor:{}".format(label), None)
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass

    # ---- fetch -----------------------------------------------------------
    async def fetch(self, url: str, tor_circuit: str = "w0",
                    extra_headers: Optional[Dict[str, str]] = None) -> FetchResult:
        transport = self._resolve_transport(url)
        host = get_host(url)
        headers = self.stealth.headers()
        if extra_headers:
            headers.update(extra_headers)  # e.g. If-None-Match / If-Modified-Since
        start = time.perf_counter()

        try:
            if transport == "tor":
                res = await self._fetch_tor(url, headers, tor_circuit)
            elif self.cfg.stealth.tls_impersonate:
                res = await self._fetch_cffi(url, headers, host)
            else:
                res = await self._fetch_httpx(url, headers, host)
        except Exception as exc:  # network / proxy / timeout
            elapsed = int((time.perf_counter() - start) * 1000)
            return FetchResult(url=url, transport=transport, elapsed_ms=elapsed,
                               error="{}: {}".format(type(exc).__name__, exc))

        res.elapsed_ms = int((time.perf_counter() - start) * 1000)
        res.transport = transport
        res.blocked = res.status in (403, 429, 503) or res.status == 0
        # enforce size cap
        cap = self.cfg.extract.max_content_bytes
        if cap and len(res.body) > cap:
            res.body = res.body[:cap]
        return res

    async def _fetch_httpx(self, url, headers, host) -> FetchResult:
        proxy = self.stealth.proxy_for(host)
        client = self._client_for(proxy)
        r = await client.get(url, headers=headers)
        return FetchResult(
            url=url, final_url=str(r.url), status=r.status_code,
            headers={k: v for k, v in r.headers.items()}, body=r.content,
            content_type=r.headers.get("content-type", ""))

    async def _fetch_tor(self, url, headers, tor_circuit) -> FetchResult:
        client = self._tor_client_for(tor_circuit)
        r = await client.get(url, headers=headers)
        return FetchResult(
            url=url, final_url=str(r.url), status=r.status_code,
            headers={k: v for k, v in r.headers.items()}, body=r.content,
            content_type=r.headers.get("content-type", ""))

    async def _fetch_cffi(self, url, headers, host) -> FetchResult:
        from curl_cffi import requests as cffi
        impersonate = self.cfg.stealth.tls_impersonate
        key = "cffi:{}".format(impersonate)
        session = self._cffi_sessions.get(key)
        if session is None:
            session = cffi.AsyncSession(impersonate=impersonate)
            self._cffi_sessions[key] = session
        proxy = self.stealth.proxy_for(host)
        proxies = {"http": proxy, "https": proxy} if proxy else None
        r = await session.get(url, headers=headers, proxies=proxies,
                              timeout=self.cfg.throttle.timeout,
                              allow_redirects=True,
                              max_redirects=self.cfg.stealth.max_redirects)
        body = r.content if isinstance(r.content, (bytes, bytearray)) else r.content.encode()
        return FetchResult(
            url=url, final_url=str(getattr(r, "url", url)), status=r.status_code,
            headers=dict(r.headers), body=bytes(body),
            content_type=r.headers.get("content-type", ""))

    async def fetch_text(self, url: str) -> Optional[tuple]:
        """Helper for robots.txt: returns (status, text) or None."""
        res = await self.fetch(url)
        if res.error:
            return None
        try:
            return res.status, res.body.decode("utf-8", errors="replace")
        except Exception:
            return res.status, ""

    async def aclose(self) -> None:
        for c in self._clients.values():
            try:
                await c.aclose()
            except Exception:
                pass
        for s in self._cffi_sessions.values():
            try:
                await s.close()  # type: ignore[attr-defined]
            except Exception:
                pass
