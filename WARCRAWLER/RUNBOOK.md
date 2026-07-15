# warcrawler — Runbook

Smoke-test every subsystem and run a full end-to-end monitor. Linux/macOS shown;
on **Windows** swap `./start.sh` → `start.bat`, and venv paths
`./.venv/bin/` → `.venv\Scripts\`.

The base install (clearnet crawling, storage, FTS, reports, alerts, focus,
sitemaps/feeds, conditional GET) needs nothing beyond Python. Tor, Redis, and
Playwright are optional and covered below.

---

## 0. Prep (once)

```bash
unzip WARCRAWLER.zip && cd WARCRAWLER
./start.sh doctor          # first run builds ./.venv and installs core deps
```
Pass: core rows `[ok]` (httpx, lxml, trafilatura, aiosqlite, warcio) and
`SQLite FTS5 [ok]`.

Install the optional stack (Tor control, Playwright, Redis, PDF, TLS) into that venv:
```bash
./.venv/bin/pip install -r requirements-full.txt
```

---

## 1. Tor

Drop a Tor binary in `tor/<os>/` from the **Tor Expert Bundle**
(`tor/linux/tor`, `tor/darwin/tor`, `tor/windows/tor.exe`). On Windows copy the
*entire* bundle `tor` folder (tor.exe **plus** any DLLs / `data`), not just the exe.

```bash
chmod +x tor/*/tor          # macOS: xattr -dr com.apple.quarantine tor/darwin/tor
./start.sh doctor           # Pass: "Tor binary: [ok]"  and  "stem [ok]"
./start.sh tor-test         # Pass: status: 200  and  {"IsTor":true,...}
```
Real onion crawl:
```bash
./start.sh run jobs/example-onion.yaml
./start.sh search --name example-onion "the"     # Pass: returns pages fetched over Tor
```
Gotchas: free **port 9050** (stop any system Tor); first bootstrap can take
10–60s; blocked networks (corp/school) may prevent Tor. See the Windows section
below if it fails there.

---

## 2. Redis (multi-process, then multi-machine)

Start a Redis server:
```bash
docker run --rm -p 6379:6379 redis:7      # separate terminal
# or install redis-server via your package manager
```
One machine:
```bash
./start.sh redis-test --redis-url redis://localhost:6379/0
# Pass: "Redis OK ... enqueued 5, claimed 5, all unique: True"
./start.sh run jobs/example-clearnet.yaml --redis-url redis://localhost:6379/0 --processes 4
# Pass: 4 processes finish; work split across them
```
Multi-machine (optional): run the **same** command on 2+ machines pointing at the
**same** `redis://REDIS_HOST:6379/0` and identical `namespace`. Verify no
double-fetching:
```bash
# on each machine after it finishes:
./start.sh export --name example-clearnet --format jsonl -o out-$(hostname).jsonl
cut -d'"' -f4 out-*.jsonl | sort | uniq -d     # Pass: empty = no URL fetched twice
```
Use a single Redis instance (not Cluster).

---

## 3. Playwright (JavaScript rendering)

```bash
./.venv/bin/playwright install chromium
./start.sh doctor          # Pass: "playwright [ok]"
```
A/B test on a JS-only page (quotes injected by JavaScript on `/js/`):
```bash
./start.sh run --seed https://quotes.toscrape.com/js/ --name js_off --max-pages 3 --same-domain
./start.sh search --name js_off "Einstein"        # likely NO hits (not rendered)

./start.sh run --seed https://quotes.toscrape.com/js/ --name js_on --render --max-pages 3 --same-domain
./start.sh search --name js_on "Einstein"         # Pass: quote text now extracted
```

---

## 4. End-to-end: login + monitor + change-detection + alerts

Open **https://webhook.site** and copy your unique URL. Create `jobs/e2e.yaml`
(the login step is a self-contained stand-in that captures a real cookie — swap
in your actual login later):

```yaml
name: e2e
seeds: ["https://quotes.toscrape.com/tag/love/"]
transport: clearnet
scope: {max_depth: 1, max_pages: 15, same_domain_only: true}
throttle: {profile: normal}
login:
  enabled: true
  method: GET
  url: "https://httpbingo.org/cookies/set?demo_session=abc123"
extract:
  watchlist: ["(?i)\\blove\\b"]
alert:
  enabled: true
  triggers: [watchlist]
  webhook_url: "https://webhook.site/YOUR-UNIQUE-ID"
  cooldown: 0
monitor: {enabled: true, recrawl_interval: 30, max_passes: 2}
output: {formats: [sqlite, jsonl], fts: true}
```
Run and inspect:
```bash
./start.sh run jobs/e2e.yaml
./start.sh alerts  --name e2e
./start.sh changes --name e2e
./start.sh report  --name e2e && open data/e2e-report.html   # Linux: xdg-open
```
Pass:
- log shows `login: ok ... captured 1 cookie(s)`, then `monitor pass 1` and `monitor pass 2`
- pass 1 fires alerts for `love`-matching pages; **pass 2 fires ~none** (unchanged)
- webhook.site shows the JSON alert payloads
- `report` opens a populated HTML brief

Then try noise control: add `cooldown: 3600` (suppress re-alerts) or
`digest: true` (one batched POST per pass) under `alert:`.

---

## Green checklist

| # | Passes if… |
|---|---|
| Tor | `tor-test` prints `"IsTor":true`; onion job fetches pages |
| Redis | `redis-test` says `all unique: True`; `--processes 4` splits work |
| Multi-machine | combined exports have **no duplicate URLs** |
| Playwright | `js_on` finds quotes that `js_off` misses |
| E2E | login captures a cookie; pass 2 stays quiet; webhook receives payloads; report renders |

If anything's off, `./start.sh doctor` tells you which piece is missing.

---

## Windows: Tor troubleshooting

`start.bat tor-test` now prints Tor's own error under
**"Tor exited during startup / Recent output:"** — read those lines and match:

**"Could not bind to 127.0.0.1:9050" (or :9051)** — the port is busy.
```bat
netstat -ano | findstr :9050
start.bat tor-test --socks-port 9250
```
(Tor Browser uses 9150 and won't clash; a second warcrawler Tor can. Close it or
change the port.)

**Immediate exit / DLL error** — the binary is incomplete. Copy the **entire**
Expert Bundle `tor` folder into `tor\windows\` (tor.exe **and** its DLLs/`data`),
then confirm it runs:
```bat
tor\windows\tor.exe --version
```

**"Bootstrapped 0%…" then timeout** — Tor launched but can't reach the network.
- Add an antivirus/Defender exclusion for `tor\windows\tor.exe` (AV often blocks it).
- Corporate/school networks frequently block Tor — try another connection.
- Confirm plain internet works: `start.bat run --seed https://example.com --name t --max-pages 1`.

**"Tor binary not found" in doctor** — wrong location or wrong working folder.
- It must be `tor\windows\tor.exe` (or on PATH), run via `start.bat` **from the
  WARCRAWLER folder**, or set `tor: { tor_binary: "C:/Tor/tor.exe" }` in the job.

**Watch Tor live** (shows the exact failing line):
```bat
tor\windows\tor.exe -f data\tor\torrc
```

### Fastest path
Install the Tor Expert Bundle anywhere and either set
`tor: { tor_binary: "C:/path/to/tor.exe" }`, or run that Tor yourself and set
`tor: { auto_start: false, socks_port: 9050 }` so warcrawler just uses it.
