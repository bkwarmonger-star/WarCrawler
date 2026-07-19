# Wave-1..3 Sonnet subagent briefs — Aegis + Bastion suite build

Nine agents, three waves. Each brief below is paste-ready: hand it to one Sonnet
subagent (ideally in its own git worktree / subdir). Every brief starts from the
**shared preamble** — prepend it to each.

Source of truth for ported code = the **three** claude.ai agent JSON exports (Aegis,
Bastion, **Armory**); the working Python is embedded in each skill's `scripts` field.
**Port + adapt + test — do not redesign** a skill that already works.

> **ARMORY LANDED (wave 0.5, in repo `armory/`).** The toolsmith's two vetted tools are
> already in-tree, self-tests green — this changes two wave-1.5/3 briefs:
> - **Agent J (`aegis/active`)** is no longer greenfield. `armory/tools/awa.py` is the DAST
>   **chassis** (scope-guard + HTTP probe + Nuclei runner + canonical-finding emit). J's job
>   shrinks to **bolting the deep probes** (SQLi/XSS/SSTI/authz/IDOR/API/SSRF via
>   `common/payloads.py`) onto AWA's existing guard + emit. Do not re-build the chassis.
> - **The canonical gate is `armory/tools/engagement.py`** (`authorize_action(target,
>   technique, at)`), not a hand-rolled one. `orchestrator/gates.py` delegates to it via
>   `gates.authorize(record, target, technique)`. Every active tool calls it.
> - **`common/records.AegisFinding` is now v1.1** — reconciled to the real AWA canonical shape
>   (`engagement_id/category/exposure/owasp_wstg/vie/source_tool/discovered_at/retest/
>   asset_in_scope`) so awa.py, the Findings Parser, VIE, and the Report Generator speak ONE
>   finding shape. Emit v1.1.
> - **`armory/catalog.json`** registers all 17 vetted tools + the `planned` build targets
>   (coverage-matrix, log-triage, ioc-enrichment, posture-grader, phishing-triage,
>   remediation-verify, siem-bridge, armory-factory, armory-coverage). Those `planned` rows
>   are the real greenfield list — everything else is a port.

---

## SHARED PREAMBLE (prepend to every agent brief)

> You are building one package of a security-tooling monorepo (offensive agent "Aegis" +
> defensive agent "Bastion", sharing MITRE ATT&CK as a coordinate system). A frozen
> `common/` contract package already exists at the repo root — import it, never fork it.
>
> **Repo layout** (packages are siblings at repo root; the existing `WARCRAWLER/` crawler
> package is untouched):
> ```
> common/      aegis/{enrich,recon_scan,active,discover,report,governance}/
> bastion/{detection,posture_triage,factory}/   bridge/   orchestrator/
> WARCRAWLER/  (existing crawler — do not modify its core)
> tests/       pyproject.toml
> ```
>
> **`common/` surface you MUST use:**
> `records` (canonical dataclasses; `.to_dict()` is your JSON output) · `schemas.validate/assert_valid` ·
> `enums` (Priority/Severity/Phase/DetectionStatus/CoverageStatus/…) · `provenance.stamp(engine, sources)` ·
> `redact.redact/redact_obj/assert_clean` · `http.get/get_json/post_json/download` (HTTPS-only, retry, cache) ·
> `attack.load/technique/resolve_group/group_techniques/parent/ATTACK_VERSION` ·
> `mappings.owasp_for/attack_for` · `ids.finding_id/hardening_id/host_key/dedupe_key/sha256` ·
> `payloads.build/safe/is_destructive/canary` (safe active-test payloads) · `oob` (OAST collaborator) ·
> `selftest.register/run_all/cli`. Read `common/CONTRACTS.md` first.
>
> **Hard rules (non-negotiable):**
> 1. Stdlib-only for cores (exception: `bastion/detection` needs `pysigma` + backends — declared).
> 2. All external HTTP goes through `common.http`. No raw `urllib` in skills.
> 3. Every record you emit: build the `common.records` dataclass, stamp `provenance`, and
>    pass `schemas.assert_valid(rec.to_dict())` in your self-test.
> 4. `redact` all free-text/evidence BEFORE it enters a record; `redact.assert_clean` in tests.
> 5. Never fabricate — CVSS/EPSS/KEV/verdicts/coverage/attribution come from code or the
>    cited public source, or are marked unknown. Preserve `provenance`.
> 6. Each skill module exposes `_self_test() -> int` (0=pass) + `--self-test` CLI + calls
>    `selftest.register("<name>", _self_test)` at import.
> 7. **Definition of Done:** `python -m <your.module> --self-test` green for every skill AND
>    `pytest tests/<yourpkg>` green. Self-tests must run OFFLINE (use bundled fixtures);
>    gate any network check behind a `--live` flag.
> 8. Safety gates (authorization, containment) are ENFORCED in `orchestrator` (agent I) —
>    do not weaken or bypass their preconditions in your skill.

---

## WAVE 1 — parallel (5 agents). Freeze `common/` before starting.

### Agent A — `aegis/enrich`  (build FIRST in the pool; B and F depend on it)
**Port:** Aegis skills *NVD CVE Enrichment* (`nvd_lookup.py` + `exploit_intel.py`) and
*Dependency & SBOM Vulnerability Analysis* (`sbom_scan.py`).
**Modules:** `aegis/enrich/nvd.py`, `aegis/enrich/exploit_intel.py`, `aegis/enrich/sbom.py`.
**Tasks:**
- Replace all `urllib` calls with `common.http`; keep NVD/EPSS/KEV/OSV/ExploitDB/Metasploit
  endpoints + caching TTLs. Read `NVD_API_KEY`/`GITHUB_TOKEN` from env only.
- Expose a STABLE public API (B and F import it): `get_cve(cve_id, include_exploits=False, include_ghsa=True) -> AegisEnrichment` and `osv_scan(components) -> {idx: [vuln]}` + `recommend(vulns)`.
- `get_cve` returns a `records.AegisEnrichment`; sbom returns `records.AegisSbomFinding[]`.
**Emit:** `aegis.enrichment`, `aegis.sbom_finding`.
**Self-test (offline):** parse a bundled NVD JSON fixture + OSV fixture → assert CVSS/CWE/KEV
fields + a fixed-version recommendation. `--live` optionally hits real NVD for one CVE.

### Agent B — `aegis/recon_scan`  (depends on A for `--enrich`)
**Port:** *Passive Recon & Scope Guard* (`recon.py`), *Web Security-Posture Checks*
(`web_posture.py`), *Scanner Output Ingestion* (`ingest.py`).
**Modules:** `aegis/recon_scan/{recon.py,web_posture.py,ingest.py}`.
**Tasks:**
- recon: crt.sh + DoH via `common.http`; keep scope-guard classify (in/out/unclassified).
- web_posture: keep the scope guard (refuse off-allowlist) + TLS-deferral note; headers via `common.http`.
- ingest: keep nmap/nessus/burp/zap/nuclei parsers + `(host,cve)|(host,title)` dedupe
  (`ids.dedupe_key`). **Change the output**: build `records.AegisFinding`, set
  `attack_techniques = mappings.attack_for(cwes, title, has_cve, kev, weaponized)` and
  `owasp = mappings.owasp_for(...)`. `--enrich` calls `aegis.enrich.nvd.get_cve`.
**Emit:** `aegis.finding[]` (+ scope report, posture findings).
**Self-test (offline):** bundled `.nessus`/`nuclei.jsonl`/nmap-xml fixtures → findings;
assert dedupe merges sources, blended priority, and every finding `assert_valid` + has `attack_techniques`.

### Agent C — `aegis/report`  (consumes `aegis.finding[]`)
**Port:** *Client Report Generator* (`report_gen.py` + `findings_library.json`),
*Compliance Mapping* (`compliance.py`), *Attack Path Narrative* (`attack_path.py`),
*Retest & Delta* (`retest_delta.py`).
**Modules:** `aegis/report/{report_gen.py,compliance.py,attack_path.py,retest_delta.py}` + `findings_library.json`.
**Tasks:** accept `AegisFinding` dicts as input. Replace the local `owasp_for`/`attack_for`
copies with `common.mappings`. Keep never-fabricate + "DRAFT" + library-match/flag-unmatched behavior.
**Emit:** report sections (JSON/MD, feeds a doc builder), retest delta buckets.
**Self-test (offline):** sample findings → exec summary + detailed findings + roadmap;
library hydration; attack-path chain references real findings; retest delta = fixed/still-open/new.

### Agent D — `bastion/detection`  (needs `common.attack`; must AUTHOR coverage_matrix)
**Port:** *Detection Engineering Engine* (`detect_engine.py`), *Threat-Actor Emulation
Planner* (`emulation_planner.py`).
**AUTHOR (missing dep):** `coverage_matrix.py` — `emulation_planner` imports it
(`cov.collect(configs)`, `cov.classify(attacked, detected, mitigated, proactive) -> (status, score, priority)`,
`cov.ATTACK_VERSION`). Build to that interface; emit `records.BastionCoverage`; status uses
`enums.CoverageStatus`. `ATTACK_VERSION` = `common.attack.ATTACK_VERSION`.
**Modules:** `bastion/detection/{detect_engine.py,coverage_matrix.py,emulation_planner.py}`.
**Tasks:** detect_engine keeps pysigma compile (Splunk/Elastic/KQL) + the emulation harness
(recall/FP from labeled replay) → `records.BastionDetection`. emulation_planner uses
`common.attack.resolve_group/group_techniques` (drop its own STIX loader) → `records.BastionEmulationPlan`.
**Deps:** `pysigma pysigma-backend-splunk pysigma-backend-elasticsearch pysigma-backend-kusto`
(declare in pyproject extras). **Self-test:** detect_engine self-test (real Sigma rule → 3 SIEMs +
emulate 100%/0%); coverage classify offline; emulation planner behind `--live` (STIX) with a cached fixture for offline.

### Agent E — `bastion/posture_triage`  (iac = port; rest = GREENFIELD to spec)
**Port:** *Cloud/IaC & Secret Posture Scanner* (`iac_posture.py`) → `records.BastionIacScan`
+ per-finding `records.BastionHardening` (CIS/D3FEND, redacted secret evidence, FP guard).
**GREENFIELD (spec in Bastion system prompt — no script exists yet):**
- `log_triage.py` → normalize JSON/syslog/CEF/CloudTrail/Windows → `records.BastionEvent`
  (+ IOC extract + `raw_ref = ids.sha256(line)`).
- `ioc_enrichment.py` → keyless-first verdicts (Shodan InternetDB, abuse.ch, OTX, CIRCL,
  RIPEstat via `common.http`) → `records.BastionIndicator` ranked by `enums.PyramidOfPain`.
- `posture_grader.py` → web/DNS/email hygiene (SPF/DKIM/DMARC, DNS via DoH; TLS via SSL Labs API server-side).
- `phishing_triage.py` → parse `.eml`, SPF/DKIM/DMARC + alignment, enrich IOCs, score → `records.BastionEmail` (actions gated).
- `remediation_verify.py` → re-run the ACTUAL check → `records.BastionRemediationVerification` (`source_finding`, `closed`, `retest_signal_to_aegis`).
**Self-test (offline):** iac fixtures (bad TF/K8s/secret + clean file → 0); log fixtures → events+IOCs;
`.eml` fixture → DMARC verdict; remediation-verify before/after fixture → closed bool.

---

## WAVE 1.5 — ACTIVE TESTING (the power layer; own/authorized apps only)

This is what makes the suite *test* apps, not just *ingest* other tools. App testing is
almost all HTTP/HTTPS → it runs natively in-sandbox, no operator-infra deferral. Two rules
bind the whole layer:

- **Allowlist-gated:** every outbound request passes through `orchestrator.gates.guard(url)`
  (host ∈ engagement allowlist + engagement `authorized()`), else the request never leaves.
  Until agent I lands, gate against a passed-in scope object with the same signature.
- **Safe-by-default:** payloads come from `common.payloads` (non-destructive; `is_destructive`
  hard-refuses); blind classes use time markers or `common.oob` canaries, never data damage;
  global rate-limit + concurrency cap + kill-switch; honor an opt-in `--aggressive` only for
  extra payloads, never for destructive ones.

### Agent J — `aegis/active`  (native DAST engine — GREENFIELD, depends on B + K)
**Modules:** `aegis/active/{engine.py,session.py,probes/*.py,evidence.py}`.
**Surface mapping:** consume the attack surface from `aegis/discover` (K) / the WARCRAWLER
bridge — endpoints, params, forms, headers, JSON/GraphQL bodies. `session.py` handles
authenticated testing (login via WARCRAWLER `login.py`, cookie/JWT/OAuth token carry,
multi-role sessions for authz tests).
**Probes (each → `records.AegisFinding` with request/response PoC evidence, redacted):**
- **Injection:** reflected + stored XSS, DOM XSS, SQLi (error/boolean/time), NoSQLi, OS
  command injection (time-safe), SSTI, XXE (OAST), LDAP, header/CRLF, path traversal/LFI,
  open redirect, SSRF (+ cloud-metadata read-only canary), host-header injection.
- **AuthN/AuthZ:** IDOR/BOLA, function-level authz (BFLA), forced browsing, auth bypass,
  session fixation, cookie flag/scope, JWT (alg=none, weak secret, `kid` tricks), OAuth/OIDC
  flow flaws, password-reset/user-enumeration, missing rate-limit on auth.
- **API:** ingest OpenAPI/Swagger + GraphQL introspection → enumerate ops → BOLA/BFLA,
  mass-assignment, excessive-data-exposure, injection on typed params, rate-limit checks.
- **Config/disclosure:** CORS misconfig, CSRF (active), clickjacking (active), verbose
  errors/stack traces, directory listing, exposed `.git/.env/backup`, secrets in responses,
  security-header posture (delegate the passive part to B), cache deception.
- **Client-side:** DOM sinks, `postMessage`, prototype pollution, dependency-confusion signals.
- Optional `fuzz.py`: differential parameter fuzzer over `payloads` + response-anomaly detection.
**Detection:** in-band via `payloads[].signatures`; boolean via response-diff (len/hash/status);
time-blind via round-trip delta vs a calibrated baseline; blind SSRF/XXE via `oob` (degrades
to "requires OAST" when `NullCollaborator`). Fingerprint tech/version → hand CVEs to `aegis/enrich`.
**Evidence:** `evidence.py` captures the exact request+response pair (redacted) + repro steps
into the finding; set `priority` by real exploitability, `attack_techniques` from the probe.
**Self-test (offline):** stand up a tiny local vulnerable WSGI app fixture (reflects a param,
error-SQLi on a quote, open-redirect param) → assert each probe fires with correct CWE +
captures PoC evidence, AND a clean endpoint yields zero findings (no false positives), AND a
request to an off-allowlist host is refused by the guard.

### Agent K — `aegis/discover`  (attack-surface mapping — depends on WARCRAWLER)
**Modules:** `aegis/discover/{crawl.py,content.py,params.py,api.py,js.py}`.
- `crawl.py` — drive WARCRAWLER (authenticated crawl, Playwright JS render, `extract`/`focus`)
  to enumerate reachable URLs, forms, and input points.
- `content.py` — wordlist content discovery (dirs/files/backups) over the WARCRAWLER frontier,
  scope-gated + rate-limited.
- `params.py` — parameter mining (query/body/headers/JSON keys); `js.py` — parse JS for
  endpoints + leaked secrets (reuse `common.redact` to avoid echoing them); `api.py` —
  discover + parse OpenAPI/Swagger/GraphQL.
Output = a surface map (endpoints + params + auth requirements) consumed by J.
**Self-test (offline):** crawl a local fixture site → assert endpoints+params extracted, a
planted `.env` flagged, and JS endpoint/secret extraction (secret redacted in output).

### Thoroughness checklist (what "thorough" covers — put in the report)
OWASP Top 10 (A01–A10) + OWASP API Top 10 + ASVS-style: access control (IDOR/BFLA), crypto/TLS
(via SSL Labs API server-side), all injection classes above, insecure design smells, security
misconfig (CORS/headers/errors/exposed files), vulnerable components (via `aegis/enrich` SBOM +
version→CVE), auth/session/JWT/OAuth, data integrity/deserialization, logging/monitoring gaps
(hand to Bastion), SSRF. Report each with: evidence PoC, CWE, ATT&CK id, exploitability, fix.

**Enrich/report upticks (fold into A and C briefs):** A adds tech-fingerprint → version → CVE →
exploit-availability matching; C's report renders the request/response PoC + reproduction steps +
exploitability rating (not just tool severity).

---

## WAVE 2 — depend on wave 1 (2 agents).

### Agent F — `aegis/governance`  (depends on A)
**Port:** *Engagement Workflow & Audit Trail* (`engagement.py`), *Continuous Exposure
Monitor* (`exposure_monitor.py`).
**Modules:** `aegis/governance/{engagement.py,exposure_monitor.py}`.
**Tasks:** engagement → `records.AegisEngagement`/`AegisAuditEntry`; keep SHA-256 hash-chain
+ `verify`; the `testing` phase gate uses `enums.Phase.GATED` + `AegisEngagement.authorized()`.
exposure_monitor imports `aegis.enrich` (sbom + nvd + exploit_intel) — drop the sys.path hack;
keep KEV/weaponized delta + exit-code-10 semantics.
**Self-test (offline):** advance→testing REFUSED without authorization then allowed after;
tamper a chain entry → `verify` fails; exposure diff on fixtures → only-new set.

### Agent G — `bastion/factory`  (skill-factory = port; rest = GREENFIELD)
**Port:** *Skill Factory* (`factory.py`) — but retarget scaffolds to emit `common.records`
+ `common.selftest.register` + the frozen gate patterns (containment gate, evidence-before-isolation,
MTTD/MTTR, never-invent-stats).
**GREENFIELD:** `regression_runner.py` (thin: import every package, `common.selftest.run_all`);
`siem_bridge.py` (backtest a detection vs telemetry fixture; stage in Splunk/Elastic DISABLED —
operator-infra + human-gated, author content only); `detection_as_code_ci.py` (compile + ATT&CK-tag +
emulation recall/FP gate for a Sigma PR).
**Self-test:** factory generates all scaffolds and each generated `--self-test` passes;
regression_runner reports N/N; ci gate fails a rule with 0% recall.

---

## WAVE 3 — integration (2 agents, last; DoD adds end-to-end).

### Agent H — `bridge/`  (consumes WARCRAWLER + aegis + bastion)
Read `WARCRAWLER/warcrawler/{engine,storage,report,alerts,diffutil,extract}.py` first.
**Build adapters:**
- `crawl_to_recon.py` — crawler-discovered hosts → `aegis.recon_scan` scope allowlist / candidate assets.
- `monitor_to_events.py` — crawler monitor + `diffutil` + `alerts` webhooks → `records.BastionEvent`
  + feed the exposure monitor / coverage overlay.
Do NOT modify warcrawler core; adapt at the boundary only.
**Self-test:** a crawl-result fixture → in-scope host list; a diff/alert fixture → `bastion.event`.

### Agent I — `orchestrator/`  (the "agent" runtime, no claude.ai wrapper) — GREENFIELD
**Build:**
- `pipeline.py` — run pipelines: recon→enrich→report; and the purple loop
  finding→hardening→detection→re-verify→retest-signal.
- `gates.py` — **authorization-as-code**: any active-scan/web-posture/exposure call is wrapped
  and returns `BLOCKED` unless the engagement is `authorized()` and target ∈ allowlist;
  **containment-as-code** (from the factory ir pattern): approval + blast-radius + rollback +
  evidence-first, else `enums.ContainmentStatus.BLOCKED`.
- `schedule.py` — cron-like runner replacing `scheduledInvocations` (weekly coverage digest, exposure monitor).
- `report_assembly.py` + audit logging via `aegis.governance`.
**DoD (end-to-end, must pass):**
1. authorized recon → scan → enrich → report produces a valid report from fixtures;
2. an `AegisFinding` → `BastionHardening` → re-verify → retest-signal loop round-trips;
3. an unauthorized active-scan call returns `BLOCKED`; an unapproved containment returns `BLOCKED`.

---

## Spawn plan

- **Wave 0 (now, human-review):** freeze `common/` (done — this pack). Land it in the repo.
- **Wave 1:** spawn A,B,C,D,E in parallel worktrees. Tell the pool to finish **A first**
  (B depends on its `get_cve`/`osv_scan` API). ~5 agents.
- **Wave 1.5 (active power layer):** spawn K (discover) then J (active DAST). J depends on
  K's surface map + B's session/scope. J is the highest-value + highest-risk skill — review
  its guard wiring + safe-payload discipline yourself.
- **Wave 2:** spawn F,G after wave 1 merges green.
- **Wave 3:** spawn H, then I. I owns the gates + end-to-end DoD — review this one yourself.

Merge rule: nothing merges unless its self-tests + `pytest` are green. Review personally:
`common/` (contracts), `aegis/active` (active-testing safety), `orchestrator/` (safety-as-code).
Every active request is allowlist-gated — set the allowlist to YOUR OWN app domains.
