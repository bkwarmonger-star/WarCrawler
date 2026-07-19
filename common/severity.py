"""
Severity + blended priority — one implementation so ingest / enrich / active agree.

"Blended" = real-world risk, not raw CVSS: CISA KEV (actively exploited) or a weaponized
Metasploit module escalates above base score; then EPSS; then CVSS. Ported from the
ingest.py priority logic so every producer ranks findings identically.
"""
from typing import Any, Dict, Optional

from .enums import Priority, Severity

_SEV_TO_P = {Severity.CRITICAL: Priority.P1, Severity.HIGH: Priority.P2,
             Severity.MEDIUM: Priority.P3, Severity.LOW: Priority.P4, Severity.INFO: Priority.P4}


def cvss_to_severity(score: Optional[float]) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return Severity.INFO
    if s >= 9.0:
        return Severity.CRITICAL
    if s >= 7.0:
        return Severity.HIGH
    if s >= 4.0:
        return Severity.MEDIUM
    if s > 0:
        return Severity.LOW
    return Severity.INFO


def blended_priority(signals: Dict[str, Any], tool_severity: str = Severity.INFO) -> str:
    """
    signals: {cvss: float, epss: float(0..1), kev: bool, weaponized: bool, public_exploit: bool}.
    Falls back to tool_severity mapping when there is no enrichment signal.
    """
    if not signals:
        return _SEV_TO_P.get(tool_severity, Priority.P3)
    cvss = _num(signals.get("cvss"))
    epss = _num(signals.get("epss"))
    if signals.get("kev") or signals.get("weaponized"):
        return Priority.P1
    if cvss >= 9.0 and epss >= 0.5:
        return Priority.P1
    if cvss >= 7.0 or signals.get("public_exploit") or epss >= 0.1:
        return Priority.P2
    if cvss >= 4.0:
        return Priority.P3
    # nothing strong from enrichment -> defer to the tool's own severity
    return _SEV_TO_P.get(tool_severity, Priority.P4)


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _self_test() -> int:
    assert cvss_to_severity(9.8) == Severity.CRITICAL
    assert cvss_to_severity(5.0) == Severity.MEDIUM
    assert blended_priority({"kev": True, "cvss": 5.0}) == Priority.P1
    assert blended_priority({"cvss": 9.9, "epss": 0.9}) == Priority.P1
    assert blended_priority({"cvss": 7.5}) == Priority.P2
    assert blended_priority({"public_exploit": True, "cvss": 3.0}) == Priority.P2
    assert blended_priority({"cvss": 4.5}) == Priority.P3
    assert blended_priority({}, tool_severity=Severity.HIGH) == Priority.P2
    print("[PASS] severity: cvss->severity + blended priority (KEV/weaponized/EPSS/CVSS ladder)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
