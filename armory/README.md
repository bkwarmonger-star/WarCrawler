# Armory — the toolsmith

Builds, imports, vets, and catalogs security tooling for Aegis/Bastion, and keeps the
arsenal measurable. Workshop is autonomous; **arming a live arsenal and any live-target
run are human-gated** (Justin Watson = final approver).

## The governing distinction
Building/importing/testing-on-sanctioned-surfaces = open workshop. Pointing a tool at a
real system = gated: needs a signed RoE with the target in scope + per-action confirm.
`tools/engagement.py` is that gate as code; the whole suite defers to it.

## What ships here (vetted, self-tests green)
- **`tools/engagement.py`** — Engagement & Scope Manager. The **canonical authorization
  gate**: `authorize_action(target, technique, at) -> {allowed, reasons}`. Enforces the
  four RoE required-elements, test window, in-scope match (domain/wildcard/CIDR/IP +
  exclusions), permitted-vs-prohibited techniques. `new | validate | check` CLI.
- **`tools/awa.py`** — Active Web Assessment. Scope-guarded HTTP probe (status/server/
  title/security-header posture → canonical findings) + a scope-guarded Nuclei runner
  (JSONL). The DAST **chassis**; deep probes bolt onto it (`common/payloads.py`).

## Catalog + coverage
`catalog.json` registers every tool across all three agents with its pipeline stage and
methodology anchor (PTES/OWASP-WSTG/MITRE-ATTACK/CWE), and marks `vetted` vs `planned` so
Armory can compute where the arsenal is blind.

## Pipeline
`Passive Recon → Active Web Assessment → Findings Parser → VIE (enrich) → Report Generator`,
governed end-to-end by the Engagement gate. Detection/hardening handoff to Bastion via
shared ATT&CK ids.

## Build discipline (the "build anything" engine — `armory-factory`, planned)
Every new tool: standard scaffold, env-only creds, rate-limiting, canonical-findings
output, evidence redaction, provenance, and a `--self-test` it must pass before `vetted`.
Promotion into a live arsenal is never silent — it's a one-click recommendation for human
approval. See `RUNNER.md` for external-binary preload.
