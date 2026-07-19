"""
Redaction — secrets/PII must NEVER reach a record, report, or log line.

Ported from the Bastion skill-factory redactor and extended. Call `redact()` on any
free-text/evidence before it enters a record; `assert_clean()` is a test-time guard.
"""
import json
import re
from typing import Any

_REDACTORS = [
    (re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|authorization)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"), "[REDACTED-BEARER]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED-AWSKEY]"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
                re.S), "[REDACTED-PRIVATE-KEY]"),
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[REDACTED-EMAIL]"),
    (re.compile(r"\b\d{13,19}\b"), "[REDACTED-PAN]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-SSN]"),
]

# Detects likely-unredacted secrets for assert_clean (post-redaction there should be none).
_LEAK = re.compile(r"(?i)(api[_-]?key|secret|password)\s*[=:]\s*[^\[\s]|AKIA[0-9A-Z]{16}|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY")


def redact(obj: Any) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    for rx, repl in _REDACTORS:
        s = rx.sub(repl, s)
    return s


def redact_obj(obj: Any) -> Any:
    """Recursively redact string values in a dict/list, preserving structure."""
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact(obj)
    return obj


def assert_clean(obj: Any) -> None:
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    m = _LEAK.search(s)
    if m:
        raise AssertionError("unredacted secret reached a record: %r" % m.group(0)[:24])
