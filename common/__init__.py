"""
common/ — the interop spine for the Aegis (offensive) + Bastion (defensive) suite.

Everything both agents produce normalizes into the canonical records defined here so
tools compose and the two halves overlay into one purple-team picture (attacked /
detected / mitigated) via shared MITRE ATT&CK technique ids.

Wave-0 contract. FROZEN before wave-1 fan-out. Additive changes bump a record's minor
version; breaking changes bump major (and the validator rejects a mismatched major).

Stdlib-only. Deterministic. No secrets in output (see redact).
"""

# Single source of truth for every canonical schema id -> semver.
# Defined BEFORE submodule imports because schemas.py reads it at import time.
SCHEMA_VERSIONS = {
    "aegis.finding": "1.1",
    "aegis.enrichment": "1.0",
    "aegis.sbom_finding": "1.0",
    "aegis.engagement": "1.0",
    "aegis.audit_entry": "1.0",
    "bastion.detection": "1.0",
    "bastion.event": "1.0",
    "bastion.indicator": "1.0",
    "bastion.hardening": "1.0",
    "bastion.incident": "1.0",
    "bastion.coverage": "1.0",
    "bastion.email": "1.0",
    "bastion.emulation_plan": "1.0",
    "bastion.iac_scan": "1.0",
    "bastion.remediation_verification": "1.0",
}

from . import (enums, provenance, redact, ids, http, attack, mappings, severity,  # noqa: E402
               payloads, oob, records, schemas, selftest)

__all__ = [
    "SCHEMA_VERSIONS",
    "enums", "provenance", "redact", "ids", "http", "attack", "mappings", "severity",
    "payloads", "oob", "records", "schemas", "selftest",
]
