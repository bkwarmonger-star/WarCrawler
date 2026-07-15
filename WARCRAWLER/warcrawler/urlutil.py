"""URL normalization, canonicalization and scope helpers.

Kept dependency-free (stdlib only) so it behaves identically on every OS.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Iterable, List, Optional, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode, unquote

# Tracking params we strip during canonicalization so ?utm_source=... does not
# create duplicate URLs. Extend via job config if needed.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "mc_cid", "mc_eid", "ref", "ref_src",
}

_DEFAULT_PORTS = {"http": 80, "https": 443}


def is_onion(host_or_url: str) -> bool:
    """True if the host (or URL) points at a Tor hidden service."""
    host = host_or_url
    if "://" in host_or_url:
        host = urlsplit(host_or_url).hostname or ""
    return host.lower().endswith(".onion")


def get_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def get_registered_domain(host: str) -> str:
    """A cheap eTLD+1 approximation (no external PSL dependency).

    Good enough for same-site scoping. IPs and onion addresses are returned
    verbatim.
    """
    host = host.lower().strip(".")
    if not host:
        return ""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if host.endswith(".onion"):
        return host
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    # Handle a handful of common two-label public suffixes.
    two_label = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "co.nz",
                 "co.jp", "com.br", "co.za"}
    if ".".join(parts[-2:]) in two_label and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def canonicalize(url: str, *, strip_tracking: bool = True,
                 keep_fragment: bool = False) -> Optional[str]:
    """Return a normalized absolute URL, or None if it is not crawlable."""
    if not url:
        return None
    url = url.strip()
    # Skip non-navigational schemes early.
    lowered = url.lower()
    for bad in ("javascript:", "mailto:", "tel:", "data:", "about:", "#"):
        if lowered.startswith(bad):
            return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    # Drop default ports.
    netloc = host
    if parts.port and _DEFAULT_PORTS.get(scheme) != parts.port:
        netloc = "{}:{}".format(host, parts.port)
    # Userinfo is preserved only if present (rare for crawling).
    if parts.username:
        auth = parts.username
        if parts.password:
            auth += ":" + parts.password
        netloc = "{}@{}".format(auth, netloc)

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if not path:
        path = "/"

    query = parts.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        if strip_tracking:
            pairs = [(k, v) for (k, v) in pairs if k.lower() not in _TRACKING_PARAMS]
        pairs.sort()
        query = urlencode(pairs)

    fragment = parts.fragment if keep_fragment else ""
    return urlunsplit((scheme, netloc, path, query, fragment))


def resolve_links(base_url: str, hrefs: Iterable[str], *,
                  strip_tracking: bool = True) -> List[str]:
    out: List[str] = []
    seen = set()
    for href in hrefs:
        if not href:
            continue
        try:
            absolute = urljoin(base_url, href.strip())
        except ValueError:
            continue
        c = canonicalize(absolute, strip_tracking=strip_tracking)
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


class ScopeFilter:
    """Decides whether a URL is in-scope for a crawl."""

    def __init__(self,
                 seeds: Sequence[str],
                 allow_domains: Optional[Sequence[str]] = None,
                 deny_domains: Optional[Sequence[str]] = None,
                 same_domain_only: bool = False,
                 include_patterns: Optional[Sequence[str]] = None,
                 exclude_patterns: Optional[Sequence[str]] = None,
                 allowed_schemes: Optional[Sequence[str]] = None):
        self.allow_domains = {d.lower() for d in (allow_domains or [])}
        self.deny_domains = {d.lower() for d in (deny_domains or [])}
        self.same_domain_only = same_domain_only
        self.allowed_schemes = {s.lower() for s in (allowed_schemes or ["http", "https"])}
        self.include = [re.compile(p) for p in (include_patterns or [])]
        self.exclude = [re.compile(p) for p in (exclude_patterns or [])]
        self.seed_domains = {get_registered_domain(get_host(s)) for s in seeds}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme.lower() not in self.allowed_schemes:
            return False
        host = (parts.hostname or "").lower()
        if not host:
            return False
        domain = get_registered_domain(host)
        if self.deny_domains and (host in self.deny_domains or domain in self.deny_domains):
            return False
        if self.same_domain_only and domain not in self.seed_domains:
            return False
        if self.allow_domains and not (host in self.allow_domains or domain in self.allow_domains):
            return False
        if self.include and not any(rx.search(url) for rx in self.include):
            return False
        if self.exclude and any(rx.search(url) for rx in self.exclude):
            return False
        return True
