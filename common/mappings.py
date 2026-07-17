"""
Shared classification tables — CWE -> OWASP Top 10 (2021) and CWE/keyword -> ATT&CK.

Frozen wave-0 reference data. The findings producer (scanner-ingest) and the report
producer (report-gen / attack-path) BOTH import this so a finding's `attack_techniques`
and `owasp` are computed one way, consistently, and the purple-team join stays coherent.

Heuristic + deterministic. Lifted from the existing report_gen.py / attack_path.py logic.
ATT&CK techniques are indicative; the tester/analyst confirms.
"""
from typing import List, Optional

# CWE -> OWASP Top 10 (2021) category label.
OWASP_CWE = {
    "CWE-79": "A03:2021 – Injection", "CWE-89": "A03:2021 – Injection",
    "CWE-77": "A03:2021 – Injection", "CWE-78": "A03:2021 – Injection",
    "CWE-94": "A03:2021 – Injection", "CWE-917": "A03:2021 – Injection", "CWE-643": "A03:2021 – Injection",
    "CWE-22": "A01:2021 – Broken Access Control", "CWE-352": "A01:2021 – Broken Access Control",
    "CWE-862": "A01:2021 – Broken Access Control", "CWE-863": "A01:2021 – Broken Access Control",
    "CWE-639": "A01:2021 – Broken Access Control", "CWE-200": "A01:2021 – Broken Access Control",
    "CWE-287": "A07:2021 – Identification and Authentication Failures",
    "CWE-306": "A07:2021 – Identification and Authentication Failures",
    "CWE-798": "A07:2021 – Identification and Authentication Failures",
    "CWE-327": "A02:2021 – Cryptographic Failures", "CWE-319": "A02:2021 – Cryptographic Failures",
    "CWE-611": "A05:2021 – Security Misconfiguration", "CWE-732": "A05:2021 – Security Misconfiguration",
    "CWE-502": "A08:2021 – Software and Data Integrity Failures",
    "CWE-918": "A10:2021 – Server-Side Request Forgery",
}
OWASP_COMPONENT = "A06:2021 – Vulnerable and Outdated Components"


def owasp_for(cwes: List[str], has_cve: bool) -> Optional[str]:
    if has_cve:
        return OWASP_COMPONENT
    for c in cwes:
        if c in OWASP_CWE:
            return OWASP_CWE[c]
    return None


# CWE -> indicative ATT&CK technique id(s).
_CWE_ATTACK = {
    "CWE-77": ["T1190", "T1059"], "CWE-78": ["T1190", "T1059"],
    "CWE-94": ["T1190", "T1059"], "CWE-917": ["T1190", "T1059"], "CWE-502": ["T1190", "T1059"],
    "CWE-89": ["T1190"], "CWE-22": ["T1190"], "CWE-918": ["T1190"],
    "CWE-79": ["T1189"],
    "CWE-287": ["T1078"], "CWE-306": ["T1078"], "CWE-798": ["T1078"],
}
_KEYWORD_ATTACK = [
    (("rce", "remote code", "command inj", "deserial"), ["T1190", "T1059"]),
    (("sql inj", "sqli"), ["T1190"]),
    (("ssrf",), ["T1190"]),
    (("xss", "cross-site scripting"), ["T1189"]),
    (("default cred", "authentication", "hardcoded"), ["T1078"]),
    (("open port",), ["T1046"]),
]


def attack_for(cwes: Optional[List[str]] = None, title: str = "",
               has_cve: bool = False, kev: bool = False, weaponized: bool = False) -> List[str]:
    """Deterministic indicative ATT&CK technique id(s) for a finding."""
    out: List[str] = []
    for c in (cwes or []):
        for t in _CWE_ATTACK.get(c, []):
            if t not in out:
                out.append(t)
    low = (title or "").lower()
    for keys, techs in _KEYWORD_ATTACK:
        if any(k in low for k in keys):
            for t in techs:
                if t not in out:
                    out.append(t)
    if not out and has_cve:
        out = ["T1190"]  # exploit public-facing application
    return sorted(set(out))
