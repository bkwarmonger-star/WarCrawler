"""
Deterministic id + key helpers — shared so ids are stable and dedupe keys match
across skills (identical inputs -> identical ids; no randomness).
"""
import hashlib
from urllib.parse import urlparse


def _h(s: str, n: int = 10) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:n].upper()


def finding_id(host: str, cve_or_title: str) -> str:
    return "FND-" + _h(f"{host_key(host)}|{cve_or_title.lower()}")


def detection_id(sigma_id_or_title: str) -> str:
    return "DET-" + _h(sigma_id_or_title)


def hardening_id(source_finding: str, title: str) -> str:
    return "HR-" + _h(f"{source_finding}|{title.lower()}")


def evidence_id(payload: str) -> str:
    return "EV-" + hashlib.sha256(payload.encode()).hexdigest()[:8].upper()


def sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


def host_key(asset: str) -> str:
    """Reduce an asset string to a bare host token for cross-tool dedupe."""
    a = (asset or "").strip().lower()
    if "://" in a:
        return urlparse(a).hostname or a
    h = a.split("/")[0]
    if h.count(":") == 1 and not h.startswith("["):
        h = h.split(":")[0]
    return h


def dedupe_key(host: str, primary_cve: str = "", title: str = "") -> tuple:
    """The (host, CVE)|(host, title) key used across ingest / retest / exposure skills."""
    hk = host_key(host)
    cve = (primary_cve or "").strip().upper()
    if cve and cve not in ("—", ""):
        return (hk, cve.split(",")[0].strip())
    return (hk, (title or "").strip().lower())
