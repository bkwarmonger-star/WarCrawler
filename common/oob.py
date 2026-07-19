"""
Out-of-band (OAST) interaction contract — for blind vuln detection (SSRF, XXE, blind
RCE, blind SQLi that has no in-band signal).

Honest constraint: the sandbox is HTTP/HTTPS egress only and cannot RECEIVE inbound
callbacks. So true OOB detection needs a collaborator server the operator controls (a
self-hosted Interactsh, a Burp Collaborator, or a tiny logging endpoint). This module is
the pluggable interface:

  * `Collaborator.host()` -> a domain to embed in payloads (`{oob}`)
  * `Collaborator.poll(token)` -> did an interaction for this token arrive?

Default `NullCollaborator` returns no host and no hits, so the engine cleanly DEGRADES to
time-based/in-band detection in-sandbox and reports blind classes as "requires OAST" rather
than silently missing them. Wire a `HttpCollaborator(base_url)` to your own server for the
full-power path. Never route OOB through a third party you don't control.
"""
from typing import Optional, Protocol

from . import http


class Collaborator(Protocol):
    def host(self, token: str) -> str: ...
    def poll(self, token: str) -> bool: ...
    def available(self) -> bool: ...


class NullCollaborator:
    """In-sandbox default: no inbound capability. Blind classes degrade to time/in-band."""
    def host(self, token: str) -> str:
        return ""

    def poll(self, token: str) -> bool:
        return False

    def available(self) -> bool:
        return False


class HttpCollaborator:
    """
    Operator-hosted collaborator. Payloads embed `<token>.<base_domain>`; `poll` asks the
    collaborator's API whether a DNS/HTTP interaction for that token was logged.
    """
    def __init__(self, base_domain: str, api_url: str, api_key: Optional[str] = None):
        self.base_domain = base_domain.strip(".")
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key

    def host(self, token: str) -> str:
        return f"{token}.{self.base_domain}"

    def available(self) -> bool:
        return bool(self.base_domain and self.api_url)

    def poll(self, token: str) -> bool:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        try:
            data = http.get_json(f"{self.api_url}/poll?token={token}", headers=headers, timeout=15)
            return bool(data.get("interactions"))
        except Exception:
            return False


def default() -> Collaborator:
    return NullCollaborator()
