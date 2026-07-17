"""
Shared enums / controlled vocabularies.

Values are the EXACT strings the existing skill scripts already emit (e.g.
"P1 (Critical)"), so porting is a straight lift with no translation layer.
Plain classes (not enum.Enum) to match the dict-based existing code.
"""


class Priority:
    P1 = "P1 (Critical)"
    P2 = "P2 (High)"
    P3 = "P3 (Medium)"
    P4 = "P4 (Low/Info)"
    ALL = (P1, P2, P3, P4)
    ORDER = {P1: 0, P2: 1, P3: 2, P4: 3}


class Severity:
    """Tool/finding severity (Nessus-style ladder)."""
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    INFO = "Info"
    ALL = (CRITICAL, HIGH, MEDIUM, LOW, INFO)
    RANK = {INFO: 0, LOW: 1, MEDIUM: 2, HIGH: 3, CRITICAL: 4}


class Phase:
    """Engagement lifecycle. Active `testing` is gated on recorded authorization."""
    SCOPING = "scoping"
    AUTHORIZATION = "authorization"
    RECON = "recon"
    TESTING = "testing"
    FINDINGS = "findings"
    REPORTING = "reporting"
    RETEST = "retest"
    CLOSED = "closed"
    ORDER = [SCOPING, AUTHORIZATION, RECON, TESTING, FINDINGS, REPORTING, RETEST, CLOSED]
    GATED = {TESTING}  # cannot enter without authorization satisfied


class DetectionStatus:
    DRAFT = "draft"
    TUNED = "tuned"
    STAGED_DISABLED = "staged-disabled"  # backtested + staged in SIEM, not yet enabled
    DEPLOYED = "deployed"                # a human enabled it
    VALIDATED = "validated"
    ALL = (DRAFT, TUNED, STAGED_DISABLED, DEPLOYED, VALIDATED)


class ContainmentStatus:
    BLOCKED = "BLOCKED"
    APPROVED_FOR_OPERATOR = "APPROVED_FOR_OPERATOR"
    ALL = (BLOCKED, APPROVED_FOR_OPERATOR)


class CoverageStatus:
    """Purple-team overlay status per ATT&CK technique."""
    CRITICAL_GAP = "CRITICAL_GAP"    # attacked, neither detected nor mitigated
    DETECT_GAP = "DETECT_GAP"        # mitigated but no detection
    MITIGATE_GAP = "MITIGATE_GAP"    # detected but no mitigation
    COVERED = "COVERED"
    PROACTIVE = "PROACTIVE"          # defended though not observed from this actor
    UNKNOWN = "UNKNOWN"
    ALL = (CRITICAL_GAP, DETECT_GAP, MITIGATE_GAP, COVERED, PROACTIVE, UNKNOWN)


class Confidence:
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    ALL = (LOW, MEDIUM, HIGH)


class TLP:
    CLEAR = "CLEAR"
    GREEN = "GREEN"
    AMBER = "AMBER"
    RED = "RED"
    ALL = (CLEAR, GREEN, AMBER, RED)


class PyramidOfPain:
    """Bogdanov's pyramid — how painful an indicator is for the adversary to change."""
    HASH = "hash_values"
    IP = "ip_addresses"
    DOMAIN = "domain_names"
    ARTIFACT = "network_host_artifacts"
    TOOL = "tools"
    TTP = "ttps"
    ORDER = [HASH, IP, DOMAIN, ARTIFACT, TOOL, TTP]  # low -> high pain
