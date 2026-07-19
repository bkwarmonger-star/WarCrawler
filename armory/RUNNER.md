# Runner tools — preload for full active power

The suite is HTTP/HTTPS-native (runs in-sandbox). A few external binaries multiply the
active-testing power; AWA (`armory/tools/awa.py`) already shells out to `nuclei` when present.
Install these on the firm's authorized runner (or the sandbox for the HTTP-only ones).

## Runs in-sandbox (HTTP/HTTPS — install and go)
| Tool | Use | Install |
|---|---|---|
| **nuclei** | templated vuln scanning (AWA `scan` mode) | `go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest` |
| **httpx** | fast HTTP probing/fingerprint | `go install github.com/projectdiscovery/httpx/cmd/httpx@latest` |
| **katana** | crawling to expand target URLs before scan | `go install github.com/projectdiscovery/katana/cmd/katana@latest` |
| **dnsx** | DNS over DoH-friendly resolution | `go install github.com/projectdiscovery/dnsx/cmd/dnsx@latest` |
| **subfinder** | passive subdomain discovery | `go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest` |

Keep a curated Nuclei template set per client stack; pin the template version (provenance).

## Own-infra only (raw sockets / arbitrary ports — NOT in-sandbox)
Produce RoE-bounded command generators + parsers for these; run on the firm's authorized
infra, ingest the output back through the Findings Parser.
- **nmap** (`--script vulners`), **naabu** (port scan), **masscan**, **Metasploit** (validation on sanctioned ranges only).

## Wired already
- **GitHub** — `gh` (browser OAuth) authenticated; repo pushes/PRs work.

## Recommended integrations (Armory's allowedIntegrations = github, airtable)
- **Airtable** as the live **catalog + coverage matrix + findings tracker** (queryable,
  persistent) instead of flat JSON. Base "Aegis Ops" with tables: Catalog, Findings,
  Coverage, Engagements. Needs the Airtable connector authorized.

## Hard rule
Every one of these only ever runs against a target the Engagement & Scope Manager
(`armory/tools/engagement.py`) authorizes. No RoE record + in-scope match = no run.
