"""
Lightweight validator — stdlib only (no jsonschema dependency).

Enforces the FROZEN contract: required fields present, enum fields in-vocabulary, and
the record's declared major version matches this build. Wave-1..3 agents call
`validate(record.to_dict())` in their self-tests so shape drift fails loudly.

This is a structural gate, not full JSON Schema. If a record needs richer validation,
add a checker to CHECKS keyed by schema name.
"""
from typing import Any, Dict, List, Tuple

from . import SCHEMA_VERSIONS
from .enums import (Priority, Severity, Phase, DetectionStatus, ContainmentStatus,
                    CoverageStatus, Confidence, TLP)

# required fields + enum constraints per schema name (name without version).
REQUIRED: Dict[str, List[str]] = {
    "aegis.finding": ["finding_id", "title", "asset", "priority"],
    "aegis.enrichment": ["cve_id"],
    "aegis.sbom_finding": ["ecosystem", "name", "version"],
    "aegis.audit_entry": ["seq", "ts", "actor", "action", "hash"],
    "aegis.engagement": ["client", "engagement_id", "phase"],
    "bastion.detection": ["detection_id", "title", "status"],
    "bastion.event": ["source"],
    "bastion.indicator": ["indicator", "type", "verdict"],
    "bastion.hardening": ["id", "title"],
    "bastion.incident": ["id"],
    "bastion.coverage": ["technique", "status"],
    "bastion.email": ["verdict"],
    "bastion.emulation_plan": ["summary"],
    "bastion.iac_scan": ["summary"],
    "bastion.remediation_verification": ["check"],
}

ENUMS: Dict[str, Dict[str, tuple]] = {
    "aegis.finding": {"priority": Priority.ALL, "tool_severity": Severity.ALL},
    "aegis.engagement": {"phase": tuple(Phase.ORDER)},
    "bastion.detection": {"status": DetectionStatus.ALL},
    "bastion.indicator": {"confidence": Confidence.ALL, "tlp": TLP.ALL},
    "bastion.coverage": {"status": CoverageStatus.ALL},
}


def _name_and_major(schema: str) -> Tuple[str, str]:
    name, _, ver = schema.partition("/")
    return name, (ver.split(".")[0] if ver else "")


def validate(record: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Return (ok, errors). Records with no `schema` field fail."""
    errors: List[str] = []
    schema = record.get("schema")
    if not schema:
        return False, ["missing 'schema' field"]
    name, major = _name_and_major(schema)
    if name not in SCHEMA_VERSIONS:
        return False, ["unknown schema: %s" % name]
    want_major = SCHEMA_VERSIONS[name].split(".")[0]
    if major and major != want_major:
        errors.append("major version mismatch: record %s, build expects %s.x" % (schema, SCHEMA_VERSIONS[name]))
    for req in REQUIRED.get(name, []):
        if record.get(req) in (None, ""):
            errors.append("missing required field: %s" % req)
    for fldname, allowed in ENUMS.get(name, {}).items():
        val = record.get(fldname)
        if val is not None and val not in allowed:
            errors.append("field %s=%r not in %s" % (fldname, val, allowed))
    return (not errors), errors


def assert_valid(record: Dict[str, Any]) -> None:
    ok, errs = validate(record)
    if not ok:
        raise AssertionError("record invalid: " + "; ".join(errs))


def _self_test() -> int:
    from . import records, provenance
    f = records.AegisFinding(finding_id="FND-1", title="x", asset="a.example.com",
                             priority=Priority.P1, provenance=provenance.stamp("test"))
    assert_valid(f.to_dict())
    bad = dict(f.to_dict(), priority="nope")
    assert not validate(bad)[0], "bad enum should fail"
    d = records.BastionDetection(detection_id="DET-1", title="t", status=DetectionStatus.DRAFT)
    assert_valid(d.to_dict())
    h = records.BastionHardening(id="HR-1", title="No DMARC", source_finding="FND-1")
    assert_valid(h.to_dict())
    assert h.to_dict()["source_finding"] == "FND-1", "Aegis->Bastion handoff key must survive"
    print("[PASS] schemas.validate: required + enum + version + handoff key")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
