# warcrawler

A portable, cross-platform crawler for **the standard web and Tor/.onion
services**. It runs straight from a USB stick — either as a prebuilt single-file
binary (no Python needed) or from source via an auto-created virtualenv — and
spins up a fleet of async worker "agents" on demand.

Unrestricted and content-neutral by default: every polite behaviour
(robots.txt, delays, concurrency caps, depth) is a knob **you** control.

---

## Highlights

- **Two transports, one engine** — native HTTP for clearnet, Tor SOCKS5 (remote
  DNS, no leaks) for `.onion`. `transport: auto` sends onion URLs through Tor and
  everything else direct.
- **Worker fleet** — async coordinator + N workers sharing a resumable frontier.
- **Fully resumable** — all state in SQLite; unplug the stick mid-crawl and
  rerun to continue exactly where you left off.
- **Stealth / anti-blocking** — rotating user-agents + header profiles, proxy
  pool with round-robin / random / sticky-host rotation, optional TLS/JA3
  browser impersonation (`curl_cffi`).
- **Tor hardening** — a bundled Tor process, per-worker **circuit isolation**
  (SOCKS auth), and **NEWNYM circuit rotation** when a target starts blocking.
- **Content pipeline** — main-article extraction (trafilatura), **PDF & plain-text
  extraction** (documents become FTS-searchable + watchlist-scannable),
  **structured data** (JSON-LD / OpenGraph / meta → clean records with author,
  dates, title, description), language detection, **SimHash near-duplicate
  detection**, CSS/XPath capture rules, and regex **watchlists**.
- **Coverage & efficient recrawls** — **sitemap ingestion** (robots.txt
  `Sitemap:` + `/sitemap.xml`, sitemap-index) and **RSS/Atom feed ingestion**
  (explicit or autodiscovered; re-read each monitor pass), **conditional GET**
  (ETag/Last-Modified → skip unchanged `304` pages), and **`Retry-After`** support.
- **Focused crawling** — best-first scheduling: score URLs by keyword/watchlist
  relevance (anchor text + URL, plus a parent bonus) and crawl the most relevant
  first, so a bounded `max_pages` surfaces what matters. Works across the memory,
  SQLite and Redis frontiers.
- **Change detection & diffs & alerts** — classify each page new / changed /
  unchanged across passes; changed pages record a **diff** (added/removed lines)
  surfaced in the alert payload and `warcrawler changes`. Alerts go to a SQLite
  log + optional webhook POST on watchlist hits. Unchanged pages never re-alert.
- **Reports** — `warcrawler report` turns a crawl into a self-contained
  HTML/Markdown intelligence brief: stats, top hosts, watchlist hits with
  snippets, recent alerts, and the latest structured records.
- **Storage & search** — SQLite with **FTS5 full-text search**, JSONL export,
  and optional **WARC** web-archive output + gzipped raw HTML.
- **JS rendering** — optional Playwright/Chromium pool for JavaScript-heavy
  sites (with stealth patches), used automatically only when a static fetch
  looks empty.
- **Monitoring** — scheduled recrawl passes to watch targets over time.

---

## Quick start (source mode)

```bash
# from the USB root
./start.sh doctor                                   # check what's available
./start.sh run --seed https://example.com --max-pages 100 --same-domain
./start.sh search --name crawl "some phrase"        # full-text search results
./start.sh export --name crawl --format csv -o out.csv
```

On Windows use `start.bat` instead of `./start.sh`. The first run creates
`.venv` on the stick and installs `requirements.txt` automatically.

> The launcher passes every argument straight to the crawler, so anything below
> that starts with `warcrawler ...` can be run as `./start.sh ...`.

### No-Python deployment (prebuilt binary)

PyInstaller can't cross-compile, so build once per OS on that OS:

```bash
./build/build.sh          # -> bin/warcrawler   (Linux/macOS)
build\build.bat           # -> bin\warcrawler.exe (Windows)
```

Copy the whole folder to the stick. `start.sh` / `start.bat` prefer a binary in
`bin/` and fall back to source mode if none is present. You can keep binaries
for several OSes side by side: `bin/linux/`, `bin/darwin/`, `bin/windows/`.

### Install as a normal package

```bash
pip install .            # then: warcrawler run ...
pip install '.[full]'    # adds Tor control, TLS impersonation, Playwright
```

---

## Commands

| Command | What it does |
|---|---|
| `run [job.yaml] [flags]` | Run a crawl from a job file and/or flags |
| `search --name NAME QUERY` | FTS5 full-text search over crawled pages |
| `export --name NAME --format csv\|jsonl -o FILE` | Dump the pages table |
| `report --name NAME [--format html\|md]` | Generate a self-contained crawl report |
| `alerts --name NAME` | List recent alerts |
| `changes --name NAME` | List recorded content changes (with diff samples) |
| `doctor` | Report optional deps, FTS5, and Tor binary status |
| `tor-test` / `redis-test` | Verify Tor / the Redis frontier |
| `init [DIR]` | Scaffold a fresh workspace with a sample job |

### Handy `run` flags (override any job file)

```
--seed URL (repeatable)     --transport auto|clearnet|tor   --tor
--workers N                 --profile polite|normal|aggressive|stealth|unlimited
--aggressive / --stealth    --ignore-robots / --respect-robots
--max-pages N   --max-depth N   --timeout SEC
--tls chrome                --render
--proxy URL (repeatable)    --allow DOMAIN / --deny DOMAIN (repeatable)
--watch REGEX (repeatable)  --same-domain / --no-same-domain
--formats sqlite,jsonl,warc,html    --data-dir DIR
--sitemaps                  --feed URL (repeatable)     --no-conditional
--focus KEYWORD (repeatable) --webhook URL   --alert-on watchlist|new|changed
--cookie "n=v" (repeatable) --header "K: V" (repeatable)
```

---

## Throttle presets

Pick with `throttle.profile:` (job file) or `--profile` / `--aggressive` /
`--stealth`. Override any individual field afterwards.

| Profile | per-host delay | per-host conc. | global conc. | robots | adaptive backoff |
|---|---|---|---|---|---|
| `polite` | 2.0s | 1 | 8 | on | on |
| `normal` | 0.5s | 2 | 16 | on | on |
| `aggressive` | 0s | 8 | 64 | **off** | on |
| `stealth` | 5.0s | 1 | 6 | off | on |
| `unlimited` | 0s | 64 | 256 | off | **off** |

**Adaptive backoff** is the only default that ever slows you down: on
`403/429/503` (or Tor block signals) it raises that host's delay and, on Tor,
rotates circuits — then decays back toward your configured speed on success. It
keeps aggressive crawls from getting you banned/flagged. Turn it fully off with
the `unlimited` profile or `throttle.adaptive_backoff: false`.

---

## Job file reference (YAML)

See `jobs/example-clearnet.yaml`, `jobs/example-onion.yaml`,
`jobs/example-monitor.yaml`. Sections: `scope`, `throttle`, `stealth`, `tor`,
`render`, `extract`, `output`, `monitor`. Anything omitted uses sane defaults.

---

## Tor / .onion

1. Put a Tor binary at `tor/<os>/tor` (see `tor/README.txt`).
2. `pip install -r requirements-full.txt` (adds `stem`).
3. `./start.sh tor-test` to verify.
4. Run an onion job or add `--tor` to any crawl.

Onion hostnames resolve **inside** Tor (remote DNS) so there are no DNS leaks.
Each worker gets an isolated circuit; blocked requests trigger NEWNYM rotation.

## Stealth

- `stealth.tls_impersonate: chrome` routes fetches through `curl_cffi` to mimic
  a real browser's TLS/JA3 fingerprint (defeats basic TLS fingerprinting).
- `stealth.proxies: [...]` with `proxy_rotation` spreads traffic across proxies.
- User-agent + header rotation is on by default; pin one with `user_agent:`.

## Authentication (login-gated sites / cookies)

warcrawler fetches; it does **not** perform the login POST itself. The workflow
is: log in once in a browser (Tor Browser for onion), copy your session cookie,
and warcrawler sends it on every request.

```yaml
user_agent: "Mozilla/5.0 ..."     # pin the SAME UA your browser used
stealth:
  rotate_user_agent: false        # don't let a rotating UA invalidate the session
  cookies:                        # convenience map -> one Cookie header
    session: "PASTE_VALUE"
    csrf: "PASTE_VALUE"
  headers:                        # or set anything verbatim (wins over the above)
    Authorization: "Bearer PASTE_TOKEN"
```

Or from the CLI (repeatable):
```
./start.sh run jobs/my.yaml --cookie "session=…" --cookie "csrf=…" \
  --header "Authorization: Bearer …"
```

Where to get the cookie: in the browser DevTools → Network tab, click a
logged-in request and copy the `Cookie` request header (or the individual
values from Application → Storage → Cookies). Sessions expire, so refresh the
value when it stops working. Pinning `user_agent` matters — many sites tie a
session to the UA it was created with.

### Programmatic login

For form-based logins, warcrawler can log itself in before crawling — optionally
scraping a CSRF token first — and reuse the captured session cookies:

```yaml
login:
  enabled: true
  url: "https://site/login"
  method: POST
  data: { username: "me", password: "secret" }
  # CSRF-protected form? fetch the page and lift the token first:
  csrf_url: "https://site/login"
  csrf_field: "csrf_token"          # hidden <input name>  (or use csrf_regex)
  success_contains: "Log out"        # confirm login worked
```

```bash
./start.sh run jobs/site.yaml \
  --login-url https://site/login --login-field username=me --login-field password=secret \
  --login-success "Log out"
```

The captured cookies are merged into `stealth.cookies`, so every crawl request
(clearnet or Tor) carries the session. It works over Tor too (the login uses the
same transport).

## JavaScript rendering

`pip install playwright && playwright install chromium`, then set
`render.enabled: true`. By default it renders **only** when a static fetch looks
empty (`render.only_when_empty`), so you pay the cost only where needed. Works
over Tor too (Chromium is pointed at the Tor SOCKS proxy).

---

## Output, search, resume

- **SQLite** (`data/<job>.sqlite`): `pages`, `links`, `frontier`, and (if
  enabled) a `pages_fts` full-text index.
- **JSONL** (`data/<job>.jsonl`): one record per page including extracted text.
- **WARC** (`data/warc/…​.warc.gz`): standards-compliant raw archive; add
  `warc` to `output.formats`.
- **Resume**: rerun the same job — `pending`/`claimed` frontier rows are
  reloaded automatically.
- **Monitor**: set `monitor.enabled: true` to recrawl on an interval.

---

## Coverage & efficient recrawls

**Sitemaps** — set `scope.use_sitemaps: true` (or `--sitemaps`) to seed the
frontier from each host's robots.txt `Sitemap:` lines and `/sitemap.xml`,
following sitemap-index files (gzip supported). Reaches pages that no link
points to. Cap with `scope.sitemap_max_urls`; override discovery with an
explicit `scope.sitemap_urls: [...]`.

**Feeds** — set `scope.use_feeds: true` (or `--feed URL`) to seed from RSS/Atom
feeds: explicit `scope.feed_urls`, or autodiscovered from your seeds'
`<link rel="alternate">` tags. Feeds are **re-read every monitor pass**, so newly
published items are picked up and flow through change-detection + alerts — ideal
for watching news, blogs, and security advisories.

**Conditional GET** — on by default (`throttle.conditional_get`). warcrawler
stores each page's `ETag`/`Last-Modified` and sends `If-None-Match` /
`If-Modified-Since` on re-fetch; a `304 Not Modified` is counted as *unchanged*
and skips re-parsing. This makes **monitor mode** cheap — you only reprocess
pages that actually changed. Disable with `--no-conditional`.

**Retry-After** — on `429`/`503` with a `Retry-After` header, warcrawler waits
exactly that long (capped by `throttle.max_retry_after`) instead of guessing.

**Documents** — PDFs and text files are fetched, their text extracted, and
indexed alongside HTML, so `search` finds phrases *inside* documents. PDF
support needs `pypdf` (in `requirements-full.txt`); without it, PDFs are still
fetched and archived, just without extracted text. Toggle with
`extract.extract_documents`.

**Structured data** — every HTML page is mined for JSON-LD (schema.org),
OpenGraph, Twitter cards, `<meta>` tags and `rel=canonical`, stored per page in
a `structured` JSON column with a normalized `summary` (title, author,
published/modified dates, description, image, site, type). No per-site rules
needed — query it in SQLite (`json_extract`) or the JSONL export. It also fills
in a missing `<title>` from `og:title` and language from `og:locale`. Toggle
with `extract.structured_data`.

---

## Alerts & change detection

Turn monitoring into signal. Each fetched page is classified **new / changed /
unchanged** (by comparing content hashes across runs), and an alert fires when
something you care about appears.

```bash
./start.sh run jobs/example-monitor.yaml --alert-on watchlist \
  --webhook https://hooks.example.com/warcrawler
./start.sh alerts --name monitor          # list recent alerts
```

```yaml
alert:
  enabled: true
  triggers: [watchlist]        # watchlist | new | changed  (NOT `on:` — YAML reads that as true)
  webhook_url: "https://..."   # optional: POST one JSON payload per alert
```

- **watchlist** — alert when a *new or changed* page matches `extract.watchlist`
- **new** — alert on any newly seen page
- **changed** — alert when a known page's content changed

Alerts are stored in the `alerts` table (view with `warcrawler alerts`) and, if
`webhook_url` is set, POSTed as `{job,url,kind,title,matches,snippet,ts}`.
**Unchanged pages never re-alert**, so recurring monitor passes stay quiet.

**What changed** — when a known page's content changes, warcrawler stores a
line-level **diff** (added/removed counts + a sample of the added text) in the
`changes` table (`warcrawler changes`), and — when `alert.include_diff` — adds
`added_lines`/`removed_lines`/`added_sample` to the webhook payload. Requires
`output.store_content` (on by default; keeps the latest page text, capped).

**Noise control** — `alert.cooldown: 3600` (or `--alert-cooldown`) suppresses
re-alerting the same URL within that many seconds (great for pages that change
every pass, like timestamps — the *change* is still recorded, just not re-alerted).
`alert.digest: true` (or `--alert-digest`) batches a whole pass's alerts into a
single webhook POST instead of one-per-hit.

---

## Focused crawling (best-first)

Crawl the most relevant pages first so a limited `max_pages` budget is spent
where it matters — ideal for threat-intel/research sweeps.

```bash
./start.sh run jobs/example-clearnet.yaml --focus breach --focus ransomware --max-pages 500
```

or in a job file:

```yaml
focus:
  enabled: true
  keywords: ["breach", "ransomware"]   # plain phrases (case-insensitive)
  patterns: ["CVE-\\d{4}-\\d+"]         # regex signals
  use_watchlist: true                   # also use extract.watchlist terms
  anchor_weight: 2.0                     # a hit in the link text counts double
  url_weight: 1.0
  parent_bonus: 3.0                      # links off a page that hit the watchlist get a boost
```

Each candidate URL is scored from its **anchor text** and **URL** before it's
fetched; the frontier dispatches the highest score first (ties stay FIFO). With
focus off, every score is 0 and crawling is ordinary breadth-first.

---

## Distributed / parallel scaling

warcrawler can share **one SQLite frontier** across many worker processes (and,
with care, machines) so they *work-steal* from a single queue — no URL is
fetched twice, and politeness is coordinated centrally.

```bash
# 8 processes on this machine, one shared frontier:
./start.sh run jobs/example-clearnet.yaml --processes 8

# enable the shared frontier without auto-spawning (launch your own workers):
./start.sh run jobs/example-clearnet.yaml --distributed
```

Or in a job file:

```yaml
fleet:
  shared: true       # use the shared SQLite frontier (work-stealing)
  lease: 300         # seconds before a stale claim is re-stealable
  network_fs: false  # true if the DB lives on a network share
```

**How it stays correct**

- **Atomic claim** — each fetch is claimed inside a `BEGIN IMMEDIATE`
  transaction, so no two workers in any process grab the same URL.
- **Central politeness** — a `hosts` table holds each host's next-available
  time and delay, so N processes behave like one polite crawler per host, with
  shared adaptive backoff.
- **Crash recovery** — a claim older than `lease` seconds becomes re-stealable,
  so a killed process never strands its URLs.
- **Global stop** — a process exits only when the whole frontier is empty.

**Multiple machines** — two options:

*Recommended: the Redis backend.* Point every machine at one Redis server and
run the same job on each:

```bash
# verify connectivity first:
./start.sh redis-test --redis-url redis://REDIS_HOST:6379/0
# then crawl (8 processes per machine, all sharing the Redis queue):
./start.sh run jobs/example-clearnet.yaml --redis-url redis://REDIS_HOST:6379/0 --processes 8
```

or in a job file:

```yaml
fleet:
  shared: true
  backend: redis
  redis_url: redis://REDIS_HOST:6379/0
  namespace: my-crawl        # shared key prefix; keep it identical on every machine
```

Claiming, per-host politeness, adaptive backoff, the hard cap, lease-based crash
recovery and global termination all run as **atomic Lua scripts inside Redis**,
so any number of machines and processes work-steal from one queue safely. URL
dedup is global. Note that *page storage* (SQLite/JSONL/WARC) is written locally
on each machine, so merge per-machine outputs afterwards. Use a single Redis
instance (not Cluster). Install with `pip install -r requirements-full.txt`.

*Alternative: a shared SQLite file* on network storage with `--distributed`.
SQLite's WAL mode does not work over network filesystems — set
`fleet.network_fs: true` (rollback journaling; slower, best-effort), and beware
that many NFS/SMB mounts have unreliable file locking. Prefer Redis for real
multi-machine loads.

Caveats:

- `max_pages` is a **hard global cap**, enforced inside the claim transaction:
  across all processes the crawl stops at exactly `max_pages` (counting pages
  processed, i.e. `claimed`+`done`), with no overshoot. On a plain resume it is
  a total-dataset cap (raise it to fetch more); in monitor mode it resets per
  pass.

Same-machine multi-process is the fully robust, recommended path.

---

## Architecture

```
cli → JobConfig ─┐
                 ▼
             Engine (coordinator)
        ┌────────┼─────────────────────────────┐
     Frontier  Workers×N                     Storage (SQLite/FTS/WARC/JSONL)
  (memory | SQLite   │
   | Redis backend)  │
    politeness,      ├── Fetcher ── clearnet (httpx) | Tor (httpx-socks) | curl_cffi
    adaptive)        ├── Robots (toggle + cache)
                     ├── Extractor (trafilatura + lxml, CSS/XPath, watchlist)
                     ├── Dedup (SimHash + banded LSH)
                     └── Renderer (Playwright, optional)
```

Files live under `warcrawler/`: `config, engine, frontier, fetcher, stealth,
robots, extract, dedup, storage, tor_manager, renderer, status, urlutil, cli`.

---

## Cross-platform notes

- Pure-relative paths; nothing is installed to the host.
- Signals: graceful Ctrl-C on all OSes (POSIX signal handlers where available,
  KeyboardInterrupt fallback on Windows).
- All core dependencies ship cross-platform wheels (Python 3.9+).

## Troubleshooting

- **Only the seed is fetched / garbled bodies** → make sure `brotli` and
  `zstandard` are installed (they're in `requirements.txt`) so compressed
  responses decode.
- **`FTS5 unavailable`** → your Python's SQLite lacks FTS5; search is disabled
  but crawling still works. Use a python.org build or conda.
- **Tor won't start** → run `./start.sh doctor`; confirm the binary path and
  that ports 9050/9051 are free.
- **Playwright errors** → `playwright install chromium` after installing it.

---

## Testing

An offline (no-network) pytest suite covers URL handling, scope rules,
SimHash/dedup, config, storage (including the unsigned-64-bit SimHash
regression), extraction, and — importantly — the shared frontier's claim
uniqueness and the hard `max_pages` cap.

```bash
./run_tests.sh            # bootstraps a venv and runs pytest
# or, in an existing environment:
pip install -r requirements-dev.txt && python -m pytest
```

For live smoke tests of Tor, Redis, and Playwright, an end-to-end
login+monitor+alerts check, and Windows Tor troubleshooting, see **`RUNBOOK.md`**.

---

See `NOTICE.md` for acceptable-use terms.
