"""Command-line interface for warcrawler."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .config import JobConfig, ThrottleConfig
from .logutil import setup_logging, get_logger

log = get_logger()


# ---------------------------------------------------------------------------
# job construction
# ---------------------------------------------------------------------------
def build_job(args) -> JobConfig:
    if getattr(args, "job_file", None):
        job = JobConfig.load(args.job_file)
    else:
        job = JobConfig.from_dict({"name": args.name or "crawl",
                                   "seeds": list(args.seed or [])})
    if getattr(args, "seed", None) and getattr(args, "job_file", None):
        job.seeds = list(dict.fromkeys(list(job.seeds) + list(args.seed)))

    profile = None
    if getattr(args, "aggressive", False):
        profile = "aggressive"
    elif getattr(args, "stealth", False):
        profile = "stealth"
    elif getattr(args, "profile", None):
        profile = args.profile
    if profile:
        job.throttle = ThrottleConfig.from_profile(profile)

    overrides = {}
    if args.workers is not None:
        overrides["workers"] = args.workers
    if args.transport:
        overrides["transport"] = args.transport
    if args.tor:
        overrides["transport"] = "tor"
        overrides["tor.enabled"] = True
    if args.max_pages is not None:
        overrides["scope.max_pages"] = args.max_pages
    if args.max_depth is not None:
        overrides["scope.max_depth"] = args.max_depth
    if args.timeout is not None:
        overrides["throttle.timeout"] = args.timeout
    if args.ignore_robots:
        overrides["throttle.respect_robots"] = False
    if args.respect_robots:
        overrides["throttle.respect_robots"] = True
    if args.tls:
        overrides["stealth.tls_impersonate"] = args.tls
    if args.render:
        overrides["render.enabled"] = True
    if args.no_same_domain:
        overrides["scope.same_domain_only"] = False
    if args.same_domain:
        overrides["scope.same_domain_only"] = True
    if args.formats:
        overrides["output.formats"] = [f.strip() for f in args.formats.split(",") if f.strip()]
    if args.socks_port is not None:
        overrides["tor.socks_port"] = args.socks_port
    if getattr(args, "distributed", False):
        overrides["fleet.shared"] = True
    if getattr(args, "lease", None) is not None:
        overrides["fleet.lease"] = args.lease
    if getattr(args, "network_fs", False):
        overrides["fleet.network_fs"] = True
    if getattr(args, "redis_url", None):
        overrides["fleet.shared"] = True
        overrides["fleet.backend"] = "redis"
        overrides["fleet.redis_url"] = args.redis_url
    if getattr(args, "focus", None):
        job.focus.enabled = True
        job.focus.keywords = list(job.focus.keywords) + list(args.focus)
    if getattr(args, "login_url", None):
        job.login.enabled = True
        job.login.url = args.login_url
        for kv in (args.login_field or []):
            if "=" in kv:
                k, v = kv.split("=", 1)
                job.login.data[k.strip()] = v.strip()
        if getattr(args, "login_success", None):
            job.login.success_contains = args.login_success
    if getattr(args, "webhook", None):
        job.alert.enabled = True
        job.alert.webhook_url = args.webhook
    if getattr(args, "alert_on", None):
        job.alert.enabled = True
        job.alert.triggers = list(args.alert_on)
    if getattr(args, "alert_cooldown", None) is not None:
        job.alert.cooldown = args.alert_cooldown
    if getattr(args, "alert_digest", False):
        job.alert.digest = True
    if getattr(args, "sitemaps", False):
        overrides["scope.use_sitemaps"] = True
    if getattr(args, "feed", None):
        job.scope.use_feeds = True
        job.scope.feed_urls = list(job.scope.feed_urls) + list(args.feed)
    if getattr(args, "no_conditional", False):
        overrides["throttle.conditional_get"] = False

    if getattr(args, "header", None):
        hdrs = dict(job.stealth.headers)
        for item in args.header:
            if ":" in item:
                k, v = item.split(":", 1)
                hdrs[k.strip()] = v.strip()
        job.stealth.headers = hdrs
    if getattr(args, "cookie", None):
        ck = dict(job.stealth.cookies)
        for item in args.cookie:
            if "=" in item:
                k, v = item.split("=", 1)
                ck[k.strip()] = v.strip()
        job.stealth.cookies = ck
    if args.proxy:
        job.stealth.proxies = list(args.proxy)
    if args.allow:
        job.scope.allow_domains = list(job.scope.allow_domains) + list(args.allow)
    if args.deny:
        job.scope.deny_domains = list(job.scope.deny_domains) + list(args.deny)
    if args.watch:
        job.extract.watchlist = list(job.extract.watchlist) + list(args.watch)

    job.apply_overrides(overrides)
    return job


def _data_dir(args) -> Path:
    return Path(args.data_dir) if args.data_dir else Path("data")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_run(args) -> int:
    job = build_job(args)
    if not job.seeds and not job.scope.feed_urls and not job.scope.sitemap_urls:
        log.error("no seeds. Pass --seed URL, --feed URL, or a job file with 'seeds:'")
        return 2
    data_dir = _data_dir(args)
    procs = max(1, getattr(args, "processes", 1) or 1)
    if procs > 1:
        job.fleet.shared = True  # multi-process requires the shared frontier
        _run_multiprocess(job, data_dir, procs, args.log_level)
        return 0
    try:
        asyncio.run(_run_async(job, data_dir))
    except KeyboardInterrupt:
        log.warning("interrupted; progress saved, rerun to resume")
    return 0


def _engine_child(job, data_dir_str: str, log_level: str, idx: int) -> None:
    """Entry point for a spawned crawler process (multi-process work-stealing)."""
    import os
    os.environ["WARCRAWLER_PLAIN_STATUS"] = "1"
    setup_logging(log_level)
    get_logger().info("crawler process %d starting", idx)
    try:
        asyncio.run(_run_async(job, Path(data_dir_str)))
    except KeyboardInterrupt:
        pass


def _run_multiprocess(job, data_dir: Path, procs: int, log_level: str) -> None:
    import copy
    import multiprocessing as mp

    tor = None
    need_tor = job.transport == "tor" or job.tor.enabled
    if need_tor and job.tor.auto_start:
        from .tor_manager import TorManager
        tor = TorManager(job.tor, base_dir=Path.cwd(), data_dir=data_dir)
        log.info("starting one shared Tor for %d processes", procs)
        tor.start_sync()

    child_job = copy.deepcopy(job)
    child_job.tor.auto_start = False  # children share the parent's Tor

    ctx = mp.get_context("spawn")
    workers = []
    log.info("launching %d crawler processes (shared frontier)", procs)
    for i in range(procs):
        p = ctx.Process(target=_engine_child,
                        args=(child_job, str(data_dir), log_level, i))
        p.start()
        workers.append(p)
    try:
        for p in workers:
            p.join()
    except KeyboardInterrupt:
        log.warning("interrupted; signalling processes")
        for p in workers:
            p.terminate()
        for p in workers:
            p.join()
    finally:
        if tor is not None:
            tor.stop_sync()
    log.info("all %d crawler processes finished", procs)


async def _run_async(job: JobConfig, data_dir: Path) -> None:
    from .engine import Engine
    from .renderer import create_renderer

    tor = None
    renderer = None
    need_tor = job.transport == "tor" or job.tor.enabled
    if need_tor and job.tor.auto_start:
        from .tor_manager import TorManager
        tor = TorManager(job.tor, base_dir=Path.cwd(), data_dir=data_dir)
        await tor.start()
    renderer = create_renderer(job)
    if renderer is not None:
        await renderer.start()

    engine = Engine(job, data_dir, tor_manager=tor, renderer=renderer)
    try:
        stats = await engine.run()
        log.info("done: %d pages, %d errors, %d dupes, %d watchlist hits, %.1f p/s",
                 stats.pages, stats.errors, stats.dupes, stats.matches, stats.rate())
    finally:
        if renderer is not None:
            await renderer.stop()
        if tor is not None:
            await tor.stop()


def cmd_search(args) -> int:
    async def _search():
        from .storage import Storage
        st = Storage(_data_dir(args), args.name)
        await st.open()
        try:
            rows = await st.search(args.query, limit=args.limit)
            if not rows:
                print("no matches")
            for url, title in rows:
                print("{}\n    {}".format(url, title or "(no title)"))
        finally:
            await st.close()
    asyncio.run(_search())
    return 0


def cmd_export(args) -> int:
    async def _export():
        from .storage import Storage
        st = Storage(_data_dir(args), args.name)
        await st.open()
        try:
            db = st._db
            cols = ["url", "final_url", "host", "depth", "status", "transport",
                    "content_type", "size", "title", "lang", "content_hash",
                    "fetched_at", "elapsed_ms"]
            async with db.execute("SELECT {} FROM pages".format(",".join(cols))) as cur:
                rows = await cur.fetchall()
            out = Path(args.output)
            if args.format == "csv":
                with open(out, "w", newline="", encoding="utf-8") as fh:
                    w = csv.writer(fh)
                    w.writerow(cols)
                    w.writerows(rows)
            else:
                with open(out, "w", encoding="utf-8") as fh:
                    for r in rows:
                        fh.write(json.dumps(dict(zip(cols, r)), ensure_ascii=False) + "\n")
            print("exported {} rows -> {}".format(len(rows), out))
        finally:
            await st.close()
    asyncio.run(_export())
    return 0


def cmd_changes(args) -> int:
    async def _c():
        import datetime
        from .storage import Storage
        st = Storage(_data_dir(args), args.name)
        await st.open()
        try:
            rows = await st.recent_changes(limit=args.limit)
            if not rows:
                print("no changes recorded")
            for ts, url, added, removed, sample in rows:
                when = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
                print("[{}] +{}/-{}  {}".format(when, added, removed, url))
                if sample:
                    print("    {}".format(sample[:200]))
        finally:
            await st.close()
    asyncio.run(_c())
    return 0


def cmd_report(args) -> int:
    from . import report as report_mod
    db = _data_dir(args) / "{}.sqlite".format(args.name)
    if not db.exists():
        log.error("no crawl database at %s", db)
        return 1
    fmt = args.format
    out = Path(args.output) if args.output else _data_dir(args) / "{}-report.{}".format(
        args.name, "md" if fmt == "md" else "html")
    content = report_mod.build(str(db), args.name, fmt=fmt, limit=args.limit)
    out.write_text(content, encoding="utf-8")
    print("report written -> {}".format(out))
    return 0


def cmd_alerts(args) -> int:
    async def _a():
        import datetime
        from .storage import Storage
        st = Storage(_data_dir(args), args.name)
        await st.open()
        try:
            rows = await st.recent_alerts(limit=args.limit)
            if not rows:
                print("no alerts")
            for ts, kind, url, title in rows:
                when = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
                print("[{}] {:<7} {}{}".format(
                    when, kind, url, ("  — " + title) if title else ""))
        finally:
            await st.close()
    asyncio.run(_a())
    return 0


def cmd_doctor(args) -> int:
    print("warcrawler {} — environment check\n".format(__version__))
    checks = [
        ("httpx (clearnet transport)", "httpx"),
        ("httpx-socks (Tor transport)", "httpx_socks"),
        ("lxml (parsing)", "lxml"),
        ("trafilatura (content extraction)", "trafilatura"),
        ("selectolax (fast parser, optional)", "selectolax"),
        ("warcio (WARC output)", "warcio"),
        ("aiosqlite (storage)", "aiosqlite"),
        ("PyYAML (job files)", "yaml"),
        ("rich (dashboard)", "rich"),
        ("stem (Tor control)", "stem"),
        ("curl_cffi (TLS impersonation)", "curl_cffi"),
        ("playwright (JS rendering)", "playwright"),
        ("redis (Redis frontier backend)", "redis"),
        ("pypdf (PDF text extraction)", "pypdf"),
    ]
    for label, mod in checks:
        try:
            __import__(mod)
            print("  [ok]   {}".format(label))
        except Exception:
            print("  [ --]  {}  (not installed)".format(label))

    # FTS5
    try:
        import sqlite3
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x);")
        con.close()
        print("  [ok]   SQLite FTS5 full-text search")
    except Exception:
        print("  [ --]  SQLite FTS5 (not available in this Python's SQLite)")

    # Tor binary
    from .tor_manager import _default_binary
    tor_bin = _default_binary(Path.cwd())
    print("  [{}]  Tor binary: {}".format("ok" if tor_bin else " --",
                                          tor_bin or "not found (see README)"))
    return 0


def cmd_tortest(args) -> int:
    async def _test():
        from .tor_manager import TorManager
        from .stealth import Stealth
        from .fetcher import Fetcher
        job = JobConfig.from_dict({"name": "tortest", "transport": "tor",
                                   "tor": {"enabled": True}})
        if args.socks_port is not None:
            job.tor.socks_port = args.socks_port
        tor = TorManager(job.tor, base_dir=Path.cwd(), data_dir=_data_dir(args))
        await tor.start()
        fetcher = Fetcher(job, Stealth(job.stealth))
        try:
            res = await fetcher.fetch("https://check.torproject.org/api/ip")
            print("status:", res.status)
            print(res.body.decode("utf-8", "replace")[:400])
        finally:
            await fetcher.aclose()
            await tor.stop()
    asyncio.run(_test())
    return 0


def cmd_redistest(args) -> int:
    async def _t():
        from .storage import Storage
        from .redis_frontier import RedisFrontier
        from .urlutil import ScopeFilter
        url = args.redis_url or "redis://localhost:6379/0"
        job = JobConfig.from_dict({
            "name": "redistest", "seeds": ["http://h.test/"],
            "fleet": {"shared": True, "backend": "redis", "redis_url": url},
            "scope": {"same_domain_only": False}})
        st = Storage(_data_dir(args), "redistest")
        await st.open()
        f = RedisFrontier(job, st, ScopeFilter(seeds=job.seeds, same_domain_only=False))
        try:
            await f.open()
            for i in range(5):
                await f.add("http://h.test/p{}".format(i), 1)
            got = []
            for _ in range(10):
                item = await f.next()
                if item is None:
                    break
                got.append(item[0])
                await f.complete(item[2], item[0], "done")
            print("Redis OK at {}".format(url))
            print("  enqueued 5, claimed {}, all unique: {}".format(
                len(got), len(set(got)) == len(got)))
            print("  remaining: {}".format(await f.remaining()))
        finally:
            await f.close()
            await st.close()
    try:
        asyncio.run(_t())
        return 0
    except Exception as exc:
        log.error("redis-test failed: %s", exc)
        return 1


_SAMPLE_JOB = """# warcrawler job file
name: example-clearnet
seeds:
  - https://example.com
transport: auto          # auto | clearnet | tor
workers: 16

scope:
  max_depth: 2
  max_pages: 200
  same_domain_only: true

throttle:
  profile: normal        # polite | normal | aggressive | stealth | unlimited
  # respect_robots: false   # uncomment to override the profile
  # per_host_delay: 0.0

stealth:
  rotate_user_agent: true
  # tls_impersonate: chrome        # requires curl_cffi
  # proxies: ["http://user:pass@host:8080"]

extract:
  extract_content: true
  detect_language: true
  # watchlist: ["(?i)breach", "bitcoin:[13][a-km-zA-HJ-NP-Z1-9]{25,34}"]
  # css_rules: {price: ".price"}

output:
  formats: [sqlite, jsonl]   # add 'warc' and/or 'html' to archive raw pages
  fts: true
"""


def cmd_init(args) -> int:
    base = Path(args.dir or ".")
    for sub in ("warcrawler", "jobs", "bin", "tor", "data", "logs"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    sample = base / "jobs" / "example-clearnet.yaml"
    if not sample.exists():
        sample.write_text(_SAMPLE_JOB, encoding="utf-8")
    print("initialized workspace at {}".format(base.resolve()))
    print("edit jobs/example-clearnet.yaml then run:  warcrawler run jobs/example-clearnet.yaml")
    return 0


# ---------------------------------------------------------------------------
# argument parser
# ---------------------------------------------------------------------------
def _add_run_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("job_file", nargs="?", help="path to a YAML job file")
    p.add_argument("--seed", action="append", help="seed URL (repeatable)")
    p.add_argument("--name", help="job name (adhoc crawls)")
    p.add_argument("--workers", type=int, help="override worker count")
    p.add_argument("--transport", choices=["auto", "clearnet", "tor"])
    p.add_argument("--tor", action="store_true", help="route everything via Tor")
    p.add_argument("--socks-port", type=int, dest="socks_port")
    p.add_argument("--profile", choices=["polite", "normal", "aggressive", "stealth", "unlimited"])
    p.add_argument("--aggressive", action="store_true", help="shortcut for --profile aggressive")
    p.add_argument("--stealth", action="store_true", help="shortcut for --profile stealth")
    p.add_argument("--ignore-robots", action="store_true", dest="ignore_robots")
    p.add_argument("--respect-robots", action="store_true", dest="respect_robots")
    p.add_argument("--max-pages", type=int, dest="max_pages")
    p.add_argument("--max-depth", type=int, dest="max_depth")
    p.add_argument("--timeout", type=float)
    p.add_argument("--tls", help="TLS impersonation profile, e.g. chrome (needs curl_cffi)")
    p.add_argument("--render", action="store_true", help="enable JS rendering (needs playwright)")
    p.add_argument("--proxy", action="append", help="proxy URL (repeatable)")
    p.add_argument("--header", action="append",
                   help='extra request header "Key: Value" (repeatable)')
    p.add_argument("--cookie", action="append",
                   help='session cookie "name=value" (repeatable)')
    p.add_argument("--login-url", dest="login_url", help="log in here before crawling")
    p.add_argument("--login-field", action="append", dest="login_field",
                   help='login form field "name=value" (repeatable)')
    p.add_argument("--login-success", dest="login_success",
                   help="text expected in the login response to confirm success")
    p.add_argument("--allow", action="append", help="allow-list domain (repeatable)")
    p.add_argument("--deny", action="append", help="deny-list domain (repeatable)")
    p.add_argument("--watch", action="append", help="watchlist regex (repeatable)")
    p.add_argument("--same-domain", action="store_true", dest="same_domain")
    p.add_argument("--no-same-domain", action="store_true", dest="no_same_domain")
    p.add_argument("--formats", help="comma list: sqlite,jsonl,warc,html")
    p.add_argument("--focus", action="append",
                   help="focus keyword to prioritize (repeatable; enables focused crawling)")
    p.add_argument("--webhook", help="POST an alert to this URL on new/changed matches")
    p.add_argument("--alert-on", action="append", dest="alert_on",
                   choices=["watchlist", "new", "changed"],
                   help="what to alert on (repeatable; enables alerting)")
    p.add_argument("--alert-cooldown", type=float, dest="alert_cooldown",
                   help="seconds to suppress re-alerting the same URL")
    p.add_argument("--alert-digest", action="store_true", dest="alert_digest",
                   help="batch each pass's alerts into one webhook POST")
    p.add_argument("--sitemaps", action="store_true",
                   help="seed from robots.txt Sitemap: and /sitemap.xml")
    p.add_argument("--feed", action="append",
                   help="RSS/Atom feed URL to seed from (repeatable; enables feeds)")
    p.add_argument("--no-conditional", action="store_true", dest="no_conditional",
                   help="disable conditional GET (always re-download)")
    # distributed / fleet scaling
    p.add_argument("--distributed", action="store_true",
                   help="use the shared SQLite frontier (work-stealing)")
    p.add_argument("--processes", type=int, default=1,
                   help="spawn N crawler processes sharing one frontier")
    p.add_argument("--lease", type=float,
                   help="seconds before a stale claim is re-stealable (default 300)")
    p.add_argument("--network-fs", action="store_true", dest="network_fs",
                   help="frontier DB is on a network share (disables WAL; best-effort)")
    p.add_argument("--redis-url", dest="redis_url",
                   help="use a Redis frontier at this URL (multi-machine work-stealing)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="warcrawler",
        description="Portable, cross-platform crawler for the standard web and Tor/.onion.")
    parser.add_argument("--version", action="version", version="warcrawler " + __version__)
    parser.add_argument("--data-dir", help="data directory (default ./data)")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--log-file", help="also write logs to this file")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run a crawl")
    _add_run_flags(p_run)
    p_run.set_defaults(func=cmd_run)

    p_search = sub.add_parser("search", help="full-text search crawled pages")
    p_search.add_argument("query")
    p_search.add_argument("--name", default="crawl", help="job name (db)")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.set_defaults(func=cmd_search)

    p_export = sub.add_parser("export", help="export pages to csv/jsonl")
    p_export.add_argument("--name", default="crawl")
    p_export.add_argument("--format", choices=["csv", "jsonl"], default="csv")
    p_export.add_argument("--output", "-o", required=True)
    p_export.set_defaults(func=cmd_export)

    p_alerts = sub.add_parser("alerts", help="list recent alerts")
    p_alerts.add_argument("--name", default="crawl")
    p_alerts.add_argument("--limit", type=int, default=50)
    p_alerts.set_defaults(func=cmd_alerts)

    p_changes = sub.add_parser("changes", help="list recorded content changes")
    p_changes.add_argument("--name", default="crawl")
    p_changes.add_argument("--limit", type=int, default=50)
    p_changes.set_defaults(func=cmd_changes)

    p_report = sub.add_parser("report", help="generate an HTML/Markdown crawl report")
    p_report.add_argument("--name", default="crawl")
    p_report.add_argument("--output", "-o", help="output path (default data/<name>-report.html)")
    p_report.add_argument("--format", choices=["html", "md"], default="html")
    p_report.add_argument("--limit", type=int, default=20)
    p_report.set_defaults(func=cmd_report)

    p_doctor = sub.add_parser("doctor", help="check optional deps and Tor")
    p_doctor.set_defaults(func=cmd_doctor)

    p_tor = sub.add_parser("tor-test", help="verify Tor connectivity")
    p_tor.add_argument("--socks-port", type=int, dest="socks_port")
    p_tor.set_defaults(func=cmd_tortest)

    p_redis = sub.add_parser("redis-test", help="verify the Redis frontier backend")
    p_redis.add_argument("--redis-url", dest="redis_url",
                         help="Redis URL (default redis://localhost:6379/0)")
    p_redis.set_defaults(func=cmd_redistest)

    p_init = sub.add_parser("init", help="scaffold a workspace")
    p_init.add_argument("dir", nargs="?", default=".")
    p_init.set_defaults(func=cmd_init)

    return parser


def main(argv: Optional[list] = None) -> int:
    import multiprocessing as mp
    mp.freeze_support()  # required for spawned processes in frozen binaries
    parser = build_parser()
    args = parser.parse_args(argv)
    log_file = Path(args.log_file) if args.log_file else None
    setup_logging(args.log_level, log_file)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
