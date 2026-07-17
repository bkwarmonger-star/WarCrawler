# Aegis + Bastion suite

Authorized purple-team security-testing suite built around the **WARCRAWLER** crawler.

- **Aegis** — offensive: recon, active DAST testing, enrichment, reporting, engagement governance.
- **Bastion** — defensive: detection engineering, posture/IaC, coverage, incident response.
- Both share **MITRE ATT&CK** technique ids as a coordinate system, so offense and defense
  overlay into one attacked / detected / mitigated picture.

## Status
Wave-0 (the frozen contract spine + safety keystone) is in place and green:
`common/` (canonical records, schemas, ATT&CK loader, http, redact, provenance, mappings,
severity, safe payload library, OAST contract) · `orchestrator/gates.py` (authorization +
scope + containment enforced in code) · `tests/` (smoke + vuln-app fixture) · CI merge gate.

Wave 1..3 packages (`aegis/*`, `bastion/*`, `bridge/`, rest of `orchestrator/`) are built by
the subagent briefs in `AGENT_BRIEFS.md`.

## Quickstart
```bash
pip install -e ".[dev]"          # add [full] for detection (pySigma) + discover (playwright)
pytest -q                         # offline smoke + package tests
python -m orchestrator.gates --self-test
python -m common.schemas          # contract self-test
```

## Authorized use only
The active-testing layer sends real requests. Every request is gated by
`orchestrator.gates` against an allowlist you control. Copy `authorization.example.json`
to `authorization.json` and list ONLY targets you own or are contracted to test. Payloads are
non-destructive by default and rate-limited. See `common/CONTRACTS.md` for the interop contract.
