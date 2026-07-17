"""
Canonical records — the interop backbone.

Design rules (FROZEN in wave 0):
  * Every record carries a `schema` field of the form "<name>/<major.minor>".
  * `to_dict()` output is exactly what the existing skill scripts already emit, so
    porting a skill = lift its script, build these records, done. No shape drift.
  * The PURPLE-TEAM JOIN KEYS are explicit fields, not conventions:
      - AegisFinding.attack_techniques  <-> ATT&CK technique ids  <-> BastionCoverage / detections
      - BastionHardening.source_finding == AegisFinding.finding_id   (Aegis -> Bastion handoff)
      - BastionRemediationVerification.source_finding closes the loop back to Aegis.
  * Secrets/PII must be redacted BEFORE building a record (see common.redact). Records
    are assumed clean; `redact.assert_clean()` is available for a belt-and-suspenders check.

Stdlib-only (dataclasses). Nested objects are plain dicts to stay JSON-trivial and to
match the existing scripts; helper constructors are provided where a shape is load-bearing.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, ClassVar, Dict, List, Optional

from . import provenance as _prov


def _clean(d: Any) -> Any:
    """Drop None values recursively so serialized records stay compact (matches skills)."""
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items() if v is not None}
    if isinstance(d, list):
        return [_clean(x) for x in d]
    return d


@dataclass
class Record:
    SCHEMA: ClassVar[str] = "record/0.0"

    def to_dict(self, drop_none: bool = True) -> Dict[str, Any]:
        d = asdict(self)
        d["schema"] = self.SCHEMA
        return _clean(d) if drop_none else d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Record":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known and k != "schema"})


# ---------------------------------------------------------------- shared sub-objects
def attack_block(techniques: Optional[List[str]] = None, tactics: Optional[List[str]] = None,
                 version: str = "") -> Dict[str, Any]:
    """The ATT&CK sub-object shared by findings, detections, coverage, emulation plans."""
    from .attack import ATTACK_VERSION
    return {"version": version or ATTACK_VERSION,
            "techniques": sorted(set(techniques or [])),
            "tactics": sorted(set(tactics or []))}


# ================================================================ AEGIS records
@dataclass
class AegisFinding(Record):
    """
    One normalized, de-duplicated finding. `to_dict()` is a superset of the ingest.py /
    report_gen.py findings-table row, plus first-class enrichment + ATT&CK join.
    """
    SCHEMA: ClassVar[str] = "aegis.finding/1.0"

    finding_id: str
    title: str                                  # "Vulnerability" column
    asset: str                                  # "Affected Asset"
    priority: str                               # enums.Priority.*
    tool_severity: str = "Info"                 # enums.Severity.*
    sources: List[str] = field(default_factory=list)   # e.g. ["nessus", "nuclei"]
    cves: List[str] = field(default_factory=list)
    cwes: List[str] = field(default_factory=list)
    cvss: Optional[Dict[str, Any]] = None       # {version, base_score, severity, vector, source}
    epss: Optional[Dict[str, Any]] = None        # {epss, percentile, date}
    kev: Optional[Dict[str, Any]] = None         # {in_kev, date_added, due_date, known_ransomware_campaign_use}
    exploit: Optional[Dict[str, Any]] = None     # {public_exploit_available, weaponized_metasploit_module, edb_count, msf_count}
    ghsa_fixed: List[str] = field(default_factory=list)
    remediation: str = ""
    attack_techniques: List[str] = field(default_factory=list)  # <-- purple-team join key
    owasp: Optional[str] = None
    evidence: str = ""                           # redacted, minimal
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AegisEnrichment(Record):
    """CVE enrichment. Keys mirror nvd_lookup.get_cve() output so the port is drop-in."""
    SCHEMA: ClassVar[str] = "aegis.enrichment/1.0"

    cve_id: str
    published: Optional[str] = None
    last_modified: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None
    cvss: Optional[Dict[str, Any]] = None
    cwe: List[str] = field(default_factory=list)
    epss: Optional[Dict[str, Any]] = None
    kev: Optional[Dict[str, Any]] = None
    exploit_intel: Optional[Dict[str, Any]] = None
    ghsa: Optional[Dict[str, Any]] = None
    affected_cpes: List[str] = field(default_factory=list)
    references: List[Dict[str, Any]] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AegisSbomFinding(Record):
    """A vulnerable dependency + upgrade target (sbom_scan.py output shape)."""
    SCHEMA: ClassVar[str] = "aegis.sbom_finding/1.0"

    ecosystem: str
    name: str
    version: str
    vulns: List[Dict[str, Any]] = field(default_factory=list)  # {id, cve, ghsa, severity, fixed[], summary}
    recommended: Optional[str] = None
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AegisAuditEntry(Record):
    """One tamper-evident, hash-chained audit line (engagement.py shape)."""
    SCHEMA: ClassVar[str] = "aegis.audit_entry/1.0"

    seq: int
    ts: str
    actor: str
    action: str
    details: str = ""
    prev_hash: str = "GENESIS"
    hash: str = ""


@dataclass
class AegisEngagement(Record):
    """Engagement state machine + audit trail. Persisted per engagement."""
    SCHEMA: ClassVar[str] = "aegis.engagement/1.0"

    client: str
    engagement_id: str
    phase: str = "scoping"
    authorization: Dict[str, Any] = field(default_factory=lambda: {
        "signed": False, "signatory": None, "allowlist": [], "window": None})
    created: Optional[str] = None
    updated: Optional[str] = None
    audit: List[Dict[str, Any]] = field(default_factory=list)

    def authorized(self) -> bool:
        a = self.authorization or {}
        return bool(a.get("signed")) and bool(a.get("allowlist"))


# ================================================================ BASTION records
@dataclass
class BastionDetection(Record):
    """Compiled + tested detection (detect_engine.py output)."""
    SCHEMA: ClassVar[str] = "bastion.detection/1.0"

    detection_id: str
    title: str
    status: str = "draft"                        # enums.DetectionStatus.*
    description: str = ""
    sigma_rule_id: Optional[str] = None
    sigma_level: str = "medium"
    author: Optional[str] = None
    attack: Dict[str, Any] = field(default_factory=attack_block)   # {version, techniques, tactics}
    required_log_source: str = "unspecified"
    false_positive_expectation: str = "medium"
    documented_false_positives: List[str] = field(default_factory=list)
    compiled_queries: Dict[str, List[str]] = field(default_factory=dict)  # {splunk_spl, elastic_lucene, microsoft_kql}
    backend_status: Dict[str, str] = field(default_factory=dict)
    test_cases: List[str] = field(default_factory=list)
    test_results: Optional[Dict[str, Any]] = None  # {recall_pct, fp_rate_pct, ...}
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BastionEvent(Record):
    """Normalized log event + extracted IOCs (log-triage output)."""
    SCHEMA: ClassVar[str] = "bastion.event/1.0"

    ts: Optional[str] = None
    source: str = ""
    message: str = ""
    host: Optional[str] = None
    user: Optional[str] = None
    iocs: Dict[str, List[str]] = field(default_factory=lambda: {
        "ipv4": [], "urls": [], "domains": [], "hashes": []})
    raw_ref: Optional[str] = None                # sha256 of raw line (chain of custody)
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BastionIndicator(Record):
    """IOC verdict, ranked by Pyramid of Pain (ioc-enrichment output)."""
    SCHEMA: ClassVar[str] = "bastion.indicator/1.0"

    indicator: str
    type: str                                    # ip / domain / url / hash
    pyramid_tier: str = "ip_addresses"           # enums.PyramidOfPain.*
    verdict: str = "unknown"                     # malicious / suspicious / benign / unknown
    confidence: str = "low"                      # enums.Confidence.*
    tlp: str = "AMBER"
    sources: List[str] = field(default_factory=list)
    enrichment: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BastionHardening(Record):
    """
    A gap -> D3FEND/CIS-mapped, verifiable remediation record.
    `source_finding` carries the Aegis finding id: THIS is the Aegis -> Bastion handoff.
    """
    SCHEMA: ClassVar[str] = "bastion.hardening/1.0"

    id: str
    title: str
    severity: str = "medium"
    cis_control: Optional[str] = None
    d3fend: Optional[str] = None
    remediation: str = ""
    verification: str = ""                        # how to re-check (feeds remediation_verification)
    source_finding: Optional[str] = None          # <-- Aegis finding id
    attack_techniques: List[str] = field(default_factory=list)
    evidence: str = ""
    # optional IaC-scan locators
    file: Optional[str] = None
    line: Optional[int] = None
    file_type: Optional[str] = None
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BastionIncident(Record):
    """Incident on NIST SP 800-61r3 / CSF-function lifecycle with MTTD/MTTR."""
    SCHEMA: ClassVar[str] = "bastion.incident/1.0"

    id: str
    severity: str = "SEV-3"
    lifecycle: List[str] = field(default_factory=lambda: [
        "Govern", "Identify", "Protect", "Detect", "Respond", "Recover"])
    evidence: List[Dict[str, Any]] = field(default_factory=list)      # hashed volatile captures
    containment_options: List[Dict[str, Any]] = field(default_factory=list)  # staged, gated
    metrics: Dict[str, Any] = field(default_factory=dict)            # {MTTD_seconds, MTTR_seconds}
    attack_techniques: List[str] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BastionCoverage(Record):
    """
    Purple-team overlay for ONE ATT&CK technique.
    A list of these IS the attacked/detected/mitigated matrix shared with Aegis.
    """
    SCHEMA: ClassVar[str] = "bastion.coverage/1.0"

    technique: str
    name: str = ""
    tactics: List[str] = field(default_factory=list)
    attacked: bool = False                        # from an Aegis finding / emulation
    detected: bool = False                        # a bastion.detection covers it
    mitigated: bool = False                       # a bastion.hardening covers it
    status: str = "UNKNOWN"                       # enums.CoverageStatus.*
    coverage_score: int = 0                        # 0..100 for Navigator gradient
    detection_ids: List[str] = field(default_factory=list)
    hardening_ids: List[str] = field(default_factory=list)


@dataclass
class BastionEmulationPlan(Record):
    """Actor-scoped emulation runlist for Aegis (emulation_planner.py output)."""
    SCHEMA: ClassVar[str] = "bastion.emulation_plan/1.0"

    summary: Dict[str, Any] = field(default_factory=dict)  # {group, group_id, aliases, techniques_used, actor_coverage_pct, critical_gaps}
    aegis_emulation_runlist: List[Dict[str, Any]] = field(default_factory=list)
    techniques: List[Dict[str, Any]] = field(default_factory=list)
    generated_at: Optional[str] = None
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BastionIacScan(Record):
    """IaC + secret scan result (iac_posture.py output)."""
    SCHEMA: ClassVar[str] = "bastion.iac_scan/1.0"

    summary: Dict[str, Any] = field(default_factory=dict)  # {files_scanned, findings, by_severity}
    findings: List[Dict[str, Any]] = field(default_factory=list)  # each ~ BastionHardening shape
    scanned_at: Optional[str] = None
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BastionEmail(Record):
    """Reported phishing email triage (phishing-triage output)."""
    SCHEMA: ClassVar[str] = "bastion.email/1.0"

    message_id: Optional[str] = None
    mail_from: Optional[str] = None
    subject: Optional[str] = None
    spf: Optional[str] = None
    dkim: Optional[str] = None
    dmarc: Optional[str] = None
    alignment: Optional[str] = None
    iocs: Dict[str, List[str]] = field(default_factory=dict)
    score: Optional[int] = None
    verdict: str = "unknown"
    recommended_actions: List[Dict[str, Any]] = field(default_factory=list)  # gated
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BastionRemediationVerification(Record):
    """
    Closed-loop proof a fix actually holds. Re-runs the ACTUAL check.
    Closes the loop Aegis finding -> Bastion hardening -> re-verify -> retest signal to Aegis.
    """
    SCHEMA: ClassVar[str] = "bastion.remediation_verification/1.0"

    source_finding: Optional[str] = None          # <-- Aegis finding id (loop close)
    technique: Optional[str] = None
    check: str = ""                               # the concrete re-check performed
    before: Optional[str] = None
    after: Optional[str] = None
    closed: bool = False
    retest_signal_to_aegis: bool = False
    provenance: Dict[str, Any] = field(default_factory=dict)


# Registry used by schemas.validate() and selftest coverage checks.
REGISTRY = {
    c.SCHEMA.split("/")[0]: c for c in [
        AegisFinding, AegisEnrichment, AegisSbomFinding, AegisAuditEntry, AegisEngagement,
        BastionDetection, BastionEvent, BastionIndicator, BastionHardening, BastionIncident,
        BastionCoverage, BastionEmulationPlan, BastionIacScan, BastionEmail,
        BastionRemediationVerification,
    ]
}
