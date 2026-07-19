"""
Wave-0 smoke test — proves the contract spine + gate + vuln-app fixture are wired.
Every wave-1..3 agent's package adds its own tests alongside this.
"""
import common
from common import records, schemas, provenance, enums, ids, severity, payloads, oob
from orchestrator import gates


def test_common_imports():
    assert set(["records", "schemas", "attack", "payloads", "oob"]).issubset(set(common.__all__))


def test_finding_validates_and_carries_join_keys():
    f = records.AegisFinding(
        finding_id=ids.finding_id("app.myapp.test", "CVE-2021-44228"),
        title="Log4Shell", asset="app.myapp.test:443/tcp",
        priority=enums.Priority.P1, attack_techniques=["T1190"],
        provenance=provenance.stamp("test"))
    schemas.assert_valid(f.to_dict())
    h = records.BastionHardening(id=ids.hardening_id(f.finding_id, "patch log4j"),
                                 title="Upgrade log4j", source_finding=f.finding_id,
                                 attack_techniques=["T1190"])
    schemas.assert_valid(h.to_dict())
    assert h.to_dict()["source_finding"] == f.finding_id  # Aegis -> Bastion handoff


def test_severity_and_payload_safety():
    assert severity.blended_priority({"kev": True}) == enums.Priority.P1
    assert payloads.is_destructive("'; DROP TABLE x--")
    assert payloads.safe(["'; DROP TABLE x--", "' OR 1=1--"]) == ["' OR 1=1--"]


def test_gate_blocks_off_allowlist():
    auth = gates.Authorization(signed=True, scope=gates.Scope(["*.myapp.test", "127.0.0.1"]))
    assert gates.guard("https://api.myapp.test/", auth)
    import pytest
    with pytest.raises(gates.OutOfScope):
        gates.guard("https://evil.example.com/", auth)
    with pytest.raises(gates.NotAuthorized):
        gates.guard("https://api.myapp.test/", gates.Authorization(signed=False))


def test_oob_degrades_in_sandbox():
    c = oob.default()
    assert not c.available() and c.host("t") == "" and c.poll("t") is False


def test_vulnapp_fixture_serves():
    from tests.fixtures import vulnapp
    import urllib.request
    base, stop = vulnapp.serve()
    try:
        body = urllib.request.urlopen(base + "/echo?q=<b>x</b>", timeout=5).read().decode()
        assert "<b>x</b>" in body  # reflected unencoded (the XSS the probe will find)
    finally:
        stop()
