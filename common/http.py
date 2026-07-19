"""
Shared HTTP client — HTTP/HTTPS only (honest sandbox model), retry/backoff, on-disk
cache, standard UA. Every skill that touches NVD / OSV / crt.sh / DoH / abuse.ch / etc.
goes through here so rate-limit handling and caching are uniform.

TLS/cert inspection is intentionally NOT done here: the sandbox may intercept HTTPS, so a
locally-observed certificate is the proxy's. Cert posture comes from a server-side source
(e.g. SSL Labs API) — see the web-posture skill's operator-command emission.
"""
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

USER_AGENT = "warcrawler-suite/1.0 (authorized-security-assessment)"
CACHE_DIR = os.path.join(tempfile.gettempdir(), "wcs_http_cache")


def _cache_path(url: str) -> str:
    import hashlib
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest() + ".cache")


def get(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 30,
        retries: int = 3, cache_ttl: int = 0, backoff: float = 3.0) -> bytes:
    """GET raw bytes. Retries on 403/429/5xx with backoff. Optional on-disk cache."""
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    if cache_ttl:
        p = _cache_path(url)
        if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < cache_ttl:
            with open(p, "rb") as f:
                return f.read()
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if cache_ttl:
                try:
                    with open(_cache_path(url), "wb") as f:
                        f.write(data)
                except OSError:
                    pass
            return data
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (403, 429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(backoff)
                continue
            raise
    assert last is not None
    raise last


def get_json(url: str, headers: Optional[Dict[str, str]] = None, **kw) -> Any:
    hh = {"Accept": "application/json"}
    if headers:
        hh.update(headers)
    return json.loads(get(url, headers=hh, **kw).decode("utf-8", "replace"))


def post_json(url: str, payload: Any, headers: Optional[Dict[str, str]] = None,
              timeout: int = 60) -> Any:
    h = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def download(url: str, dest: str, timeout: int = 120, cache_ttl: int = 0) -> str:
    """Download to a path (large catalogs: ATT&CK STIX, KEV, ExploitDB CSV)."""
    if cache_ttl and os.path.exists(dest) and (time.time() - os.path.getmtime(dest)) < cache_ttl:
        return dest
    data = get(url, timeout=timeout)
    with open(dest, "wb") as f:
        f.write(data)
    return dest
