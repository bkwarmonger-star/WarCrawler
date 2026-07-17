# `common/` — the frozen wave-0 contract

Everything both agents produce normalizes into these records so the two halves overlay
into **one purple-team picture** (attacked / detected / mitigated) via shared MITRE
ATT&CK technique ids. Freeze this before wave-1 fan-out; wave-1..3 agents code against
these interfaces, never against each other.

## Module surface (the import contract)

| Module | Provides | Every skill uses it for |
|---|---|---|
| `records` | dataclasses for all 15 canonical records + `REGISTRY` | building typed output; `.to_dict()` == the JSON the old scripts emit |
| `schemas` | `validate(dict) -> (ok, errors)`, `assert_valid` | gating output shape in self-tests |
| `enums` | `Priority`, `Severity`, `Phase`, `DetectionStatus`, `ContainmentStatus`, `CoverageStatus`, `Confidence`, `TLP`, `PyramidOfPain` | controlled vocab (exact legacy strings) |
| `provenance` | `stamp(engine, sources)` , `utcnow()` | the mandatory `provenance` field |
| `redact` | `redact`, `redact_obj`, `assert_clean` | scrub secrets/PII **before** a record is built |
| `http` | `get/get_json/post_json/download` (HTTPS-only, retry, cache) | all external calls (NVD/OSV/crt.sh/DoH/abuse.ch/…) |
| `attack` | `load`, `technique`, `resolve_group`, `group_techniques`, `parent`, `ATTACK_VERSION` | the shared ATT&CK coordinate system |
| `ids` | `finding_id`, `detection_id`, `hardening_id`, `host_key`, `dedupe_key`, `sha256` | stable ids + cross-tool dedupe keys |
| `selftest` | `register`, `run_all`, `cli` | the `--self-test` gate + regression runner |

## The three purple-team join keys (non-negotiable)

1. **`AegisFinding.attack_techniques[]` ↔ `BastionCoverage.technique` ↔ `BastionDetection.attack.techniques[]`**
   The ATT&CK technique id is the join between offense and defense. Every finding,
   detection, hardening, and coverage row carries it.
2. **`BastionHardening.source_finding == AegisFinding.finding_id`**
   The Aegis → Bastion handoff. A hardening record always points back at the finding
   that motivated it.
3. **`BastionRemediationVerification.source_finding` (+ `retest_signal_to_aegis`)**
   Closes the loop: finding → hardening → re-verify the actual check → signal Aegis to retest.

## Versioning rule

`schema` field = `"<name>/<major.minor>"`. Additive field → bump **minor**. Breaking
change → bump **major**; `schemas.validate()` rejects a record whose major ≠ this build's.
`SCHEMA_VERSIONS` in `common/__init__.py` is the single source of truth.

## Self-test gate (Definition of Done)

Every skill module exposes `_self_test() -> int` (0 = pass) and a `--self-test` CLI flag,
and calls `selftest.register(name, fn)` at import. A package is DONE when:
`python -m <pkg> --self-test` is green **and** `pytest` is green. Nothing merges red.

## Safety-as-code (moves from prompt → enforced code in wave 3)

The Aegis **authorization gate** (signed scope + non-empty allowlist before any active
touch; active `testing` phase blocked otherwise) and the Bastion **containment gate**
(human approval + blast-radius + rollback, and volatile evidence captured first for
isolation) are behavioral in the claude.ai agents. In this standalone suite they must be
**enforced functions** at the orchestrator/`common` boundary — a scan/containment call
that lacks the preconditions returns `BLOCKED`, it does not run. `enums.ContainmentStatus`
+ `enums.Phase.GATED` are the vocabulary; agent I (orchestrator) implements enforcement.

## Never-fabricate

Grades, compiled queries, verdicts, coverage, CVSS/EPSS, hit counts, and threat-actor
attribution come from code or authoritative public sources — never invented. `redact`
guarantees secrets never reach a record; `provenance` records where each fact came from.
