"""
Safety-as-code — the keystone gate every active/containment action routes through.

This is where the Aegis authorization gate and the Bastion containment gate stop being
prompt behavior and become ENFORCED code. An active-scan request that is not authorized
+ in-scope + in-window never leaves; a containment action without approval + blast-radius +
rollback (+ evidence-first for isolation) returns BLOCKED and does not execute.

The active-testing layer (aegis/active, aegis/discover) MUST call `guarded_get` / `guard`
for every outbound request — set the allowlist to targets you are authorized to test
(for own-app testing: your own domains). Stdlib-only; loads a JSON authorization file.

Provided at wave 0 so the active layer can build against a stable guard signature.
"""
from __future__ import annotations

import ipaddress
import json
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from common import http
from common.enums import ContainmentStatus
from common.ids import host_key


class NotAuthorized(Exception):
    """Raised when the engagement is not authorized (no signed scope / allowlist)."""


class OutOfScope(Exception):
    """Raised when a target host is not on the authorized allowlist."""


class WindowClosed(Exception):
    """Raised when now() is outside the authorized test window."""


# ---------------------------------------------------------------- scope allowlist
@dataclass
class Scope:
    """Authorized allowlist: exact hosts, *.wildcards, CIDRs/IPs (recon-style)."""
    entries: List[str] = field(default_factory=list)

    def _parts(self):
        nets, hosts, wild = [], set(), []
        for e in self.entries:
            e = str(e).strip().lower().rstrip(".")
            if not e:
                continue
            try:
                nets.append(ipaddress.ip_network(e, strict=False))
                continue
            except ValueError:
                pass
            if e.startswith("*."):
                wild.append(e[2:])
            elif e.startswith("."):
                wild.append(e[1:])
            else:
                hosts.add(e)
        return nets, hosts, wild

    def contains(self, host_or_url: str) -> bool:
        h = host_key(host_or_url)
        nets, hosts, wild = self._parts()
        if h in hosts:
            return True
        for w in wild:
            if h == w or h.endswith("." + w):
                return True
        try:
            ip = ipaddress.ip_address(h)
            return any(ip in n for n in nets)
        except ValueError:
            return False

    def __bool__(self):
        return bool([e for e in self.entries if str(e).strip()])


@dataclass
class Authorization:
    """Signed authorization / rules of engagement. `satisfied()` gates active testing."""
    signed: bool = False
    signatory: Optional[str] = None
    scope: Scope = field(default_factory=Scope)
    window_start: Optional[str] = None   # "YYYY-MM-DD"
    window_end: Optional[str] = None

    def satisfied(self) -> bool:
        return bool(self.signed) and bool(self.scope)

    def in_window(self, today: Optional[date] = None) -> bool:
        if not (self.window_start or self.window_end):
            return True
        d = today or date.today()
        if self.window_start and d < date.fromisoformat(self.window_start):
            return False
        if self.window_end and d > date.fromisoformat(self.window_end):
            return False
        return True

    @classmethod
    def from_file(cls, path: str) -> "Authorization":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return cls.from_dict(d)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Authorization":
        w = d.get("window", {}) or {}
        return cls(signed=bool(d.get("signed")), signatory=d.get("signatory"),
                   scope=Scope(d.get("allowlist", [])),
                   window_start=w.get("start"), window_end=w.get("end"))


# ---------------------------------------------------------------- the guard
def guard(url: str, auth: Authorization, today: Optional[date] = None) -> str:
    """
    Enforce authorization + scope + window for an ACTIVE request. Returns the url if
    permitted; raises otherwise. Call this before ANY outbound active-test request.
    """
    if not auth.satisfied():
        raise NotAuthorized("no signed authorization + non-empty allowlist on file")
    if not auth.in_window(today):
        raise WindowClosed("outside authorized test window %s..%s" % (auth.window_start, auth.window_end))
    if not auth.scope.contains(url):
        raise OutOfScope("%s is not on the authorized allowlist" % host_key(url))
    return url


class RateLimiter:
    """Per-host minimum interval — politeness / non-DoS discipline for active testing."""
    def __init__(self, min_interval: float = 0.2):
        self.min_interval = min_interval
        self._last: Dict[str, float] = {}

    def wait(self, url: str) -> None:
        h = host_key(url)
        now = time.monotonic()
        delta = now - self._last.get(h, 0.0)
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last[h] = time.monotonic()


_DEFAULT_RL = RateLimiter()


def guarded_get(url: str, auth: Authorization, rate: Optional[RateLimiter] = None, **kw):
    """common.http.get, but only after the guard passes + rate limit is honored."""
    guard(url, auth)
    (rate or _DEFAULT_RL).wait(url)
    return http.get(url, **kw)


# ---------------------------------------------------------------- containment gate
APPROVED_RESPONSE_ACTIONS = {"isolate_host", "block_ip", "block_domain", "disable_account",
                             "kill_process", "quarantine_file"}
ISOLATION_ACTIONS = {"isolate_host", "quarantine_file"}  # require volatile evidence first


class ContainmentNotApproved(Exception):
    pass


def stage_containment(action: str, target: str, approved: bool = False,
                      blast_radius: Optional[str] = None, rollback: Optional[str] = None,
                      evidence_captured: bool = False) -> Dict[str, Any]:
    """
    A containment action CANNOT reach APPROVED_FOR_OPERATOR without human approval + a
    blast-radius statement + a rollback plan; isolation also needs volatile evidence first.
    Unknown/unsafe actions raise. (Ported from the Bastion skill-factory ir gate.)
    """
    if action not in APPROVED_RESPONSE_ACTIONS:
        raise ContainmentNotApproved("unknown or unsafe action: %r" % action)
    staged = {"action": action, "target": target, "blast_radius": blast_radius,
              "rollback": rollback, "evidence_captured": bool(evidence_captured)}
    if not (approved and blast_radius and rollback):
        staged["status"] = ContainmentStatus.BLOCKED
        staged["reason"] = "requires human approval + blast_radius + rollback"
    elif action in ISOLATION_ACTIONS and not evidence_captured:
        staged["status"] = ContainmentStatus.BLOCKED
        staged["reason"] = "capture volatile evidence before isolation"
    else:
        staged["status"] = ContainmentStatus.APPROVED_FOR_OPERATOR
    return staged


# ---------------------------------------------------------------- self-test
def _self_test() -> int:
    print("=== orchestrator.gates :: SELF-TEST ===")
    auth = Authorization(signed=True, signatory="owner@myapp.test",
                         scope=Scope(["app.myapp.test", "*.myapp.test", "127.0.0.1"]))
    assert guard("https://app.myapp.test/login", auth)
    assert guard("http://api.myapp.test/v1", auth)
    for bad in ("https://evil.example.com/", "https://myapp.test.attacker.com/"):
        try:
            guard(bad, auth); raise AssertionError("should have blocked " + bad)
        except OutOfScope:
            pass
    try:
        guard("https://app.myapp.test/", Authorization(signed=False)); raise AssertionError("unauth")
    except NotAuthorized:
        pass
    # window
    closed = Authorization(signed=True, scope=Scope(["a.test"]), window_start="2000-01-01", window_end="2000-01-02")
    try:
        guard("https://a.test/", closed); raise AssertionError("window")
    except WindowClosed:
        pass
    # containment gate
    assert stage_containment("isolate_host", "10.0.0.5")["status"] == ContainmentStatus.BLOCKED
    assert stage_containment("isolate_host", "10.0.0.5", approved=True, blast_radius="1 host",
                             rollback="re-enable NIC")["status"] == ContainmentStatus.BLOCKED  # no evidence
    ok = stage_containment("isolate_host", "10.0.0.5", approved=True, blast_radius="1 host",
                           rollback="re-enable NIC", evidence_captured=True)
    assert ok["status"] == ContainmentStatus.APPROVED_FOR_OPERATOR
    try:
        stage_containment("wipe_disk", "x"); raise AssertionError("unsafe")
    except ContainmentNotApproved:
        pass
    print("[PASS] scope allowlist (incl. suffix-spoof block), auth gate, window gate, containment gate")
    print("=== SELF-TEST PASSED ===")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
