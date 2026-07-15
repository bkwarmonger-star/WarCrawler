"""The crawl engine: coordinator plus an async worker fleet."""
from __future__ import annotations

import asyncio
import signal
import time
from pathlib import Path
from typing import List, Optional, Tuple

from . import documents
from .alerts import Alerter
from .config import JobConfig
from .dedup import DedupIndex
from .extract import Extractor
from .fetcher import Fetcher, FetchResult, parse_retry_after
from .focus import Scorer
from .frontier import Frontier
from .sqlite_frontier import SQLiteFrontier
from .logutil import get_logger
from .robots import RobotsCache
from .stealth import Stealth
from .storage import Storage
from .urlutil import ScopeFilter, canonicalize, get_host, is_onion

log = get_logger()


class Stats:
    def __init__(self):
        self.start = time.time()
        self.pages = 0
        self.errors = 0
        self.skipped = 0
        self.dupes = 0
        self.matches = 0
        self.bytes = 0
        self.unchanged = 0   # 304 Not Modified (conditional GET)
        self.sitemap = 0     # URLs seeded from sitemaps
        self.feed = 0        # URLs seeded from feeds
        self.alerts = 0      # alerts fired
        self.changed = 0     # pages whose content changed since last crawl

    def rate(self) -> float:
        elapsed = max(1e-6, time.time() - self.start)
        return self.pages / elapsed


class Engine:
    def __init__(self, cfg: JobConfig, data_dir: Path,
                 tor_manager=None, renderer=None):
        self.cfg = cfg
        self.data_dir = Path(data_dir)
        self.tor = tor_manager
        self.renderer = renderer
        self.stats = Stats()
        self.stop = asyncio.Event()

        self.scope = ScopeFilter(
            seeds=cfg.seeds,
            allow_domains=cfg.scope.allow_domains,
            deny_domains=cfg.scope.deny_domains,
            same_domain_only=cfg.scope.same_domain_only,
            include_patterns=cfg.scope.include_patterns,
            exclude_patterns=cfg.scope.exclude_patterns,
            allowed_schemes=cfg.scope.allowed_schemes,
        )
        self.storage = Storage(self.data_dir, cfg.name,
                               formats=cfg.output.formats, fts=cfg.output.fts,
                               save_raw_html=cfg.output.save_raw_html,
                               network_fs=cfg.fleet.network_fs)
        self.stealth = Stealth(cfg.stealth, pinned_user_agent=cfg.user_agent)
        self.fetcher = Fetcher(cfg, self.stealth)
        self.shared = bool(cfg.fleet.shared)
        if self.shared and cfg.fleet.backend == "redis":
            from .redis_frontier import RedisFrontier
            self.frontier = RedisFrontier(cfg, self.storage, self.scope)
        elif self.shared:
            self.frontier = SQLiteFrontier(cfg, self.storage, self.scope)
        else:
            self.frontier = Frontier(cfg, self.storage, self.scope)
        self.extractor = Extractor(cfg.extract)
        self.dedup = DedupIndex(max_distance=3)
        robots_ua = cfg.user_agent or "warcrawler"
        self.robots = RobotsCache(cfg.throttle.respect_robots,
                                  self.fetcher.fetch_text, user_agent=robots_ua)
        # Monotonic counter for fresh Tor SOCKS identities on circuit rotation.
        self._rot_seq = 0
        # Focused crawling: score URLs so the most relevant are crawled first.
        self.scorer = Scorer(cfg.focus, cfg.extract.watchlist) if cfg.focus.enabled else None
        # Change detection + alerting.
        self.alerter = Alerter(cfg.alert) if cfg.alert.enabled else None

    # ---- lifecycle -------------------------------------------------------
    async def setup(self) -> None:
        await self.storage.open()
        await self.frontier.open()
        # Log in first so every subsequent request carries the session.
        if self.cfg.login.enabled and self.cfg.login.url:
            from .login import perform_login
            proxy = self.stealth.proxy_for("") if self.cfg.stealth.proxies else None
            res = await perform_login(self.cfg.login, transport=self.cfg.transport,
                                      tor_socks_port=self.cfg.tor.socks_port, proxy=proxy,
                                      timeout=self.cfg.throttle.timeout)
            cookies = res.get("cookies") or {}
            if cookies:
                self.cfg.stealth.cookies.update(cookies)
            log.info("login: %s (status %s), captured %d cookie(s)",
                     "ok" if res.get("ok") else "unconfirmed",
                     res.get("status"), len(cookies))
        for h in await self.storage.load_simhashes():
            self.dedup.add(h)
        resumed = 0
        if self.shared:
            # Pending work already lives in the shared frontier (SQLite/Redis)
            # and is claimed on demand — nothing to load. Report the backlog.
            resumed = await self.frontier.remaining()
        else:
            for url, depth in await self.storage.load_pending():
                self.frontier.add_loaded(url, depth)
                resumed += 1
            # Count pages already done toward the hard max_pages cap.
            self.frontier.set_baseline((await self.storage.stats()).get("done", 0))
        # Seed (canonicalize first). INSERT OR IGNORE makes this safe when
        # several processes seed the same shared frontier at once.
        for seed in self.cfg.seeds:
            c = canonicalize(seed)
            if c:
                await self.frontier.add(c, 0)
        if self.cfg.scope.use_sitemaps:
            await self._seed_from_sitemaps()
        if self.cfg.scope.use_feeds:
            await self._seed_from_feeds()
        log.info("job '%s': %d seed(s), %d resumed, transport=%s, workers=%d, mode=%s",
                 self.cfg.name, len(self.cfg.seeds), resumed,
                 self.cfg.transport, self.cfg.effective_workers(),
                 "shared" if self.shared else "memory")

    async def _seed_from_sitemaps(self) -> None:
        from . import sitemap as sm
        from urllib.parse import urlsplit
        sm_urls = list(self.cfg.scope.sitemap_urls)
        if not sm_urls:
            bases = set()
            for seed in self.cfg.seeds:
                c = canonicalize(seed)
                if not c:
                    continue
                p = urlsplit(c)
                base = "{}://{}".format(p.scheme, p.netloc)
                if base in bases:
                    continue
                bases.add(base)
                r = await self.fetcher.fetch(base + "/robots.txt")
                if r and not r.error and r.status == 200:
                    sm_urls += sm.sitemaps_from_robots(r.body.decode("utf-8", "replace"))
                sm_urls.append(sm.default_sitemap(base))

        async def fetch_bytes(u):
            r = await self.fetcher.fetch(u)
            return None if (r is None or r.error) else r.body

        urls = await sm.gather_urls(fetch_bytes, sm_urls, self.cfg.scope.sitemap_max_urls)
        added = 0
        for u in urls:
            c = canonicalize(u)
            if c and await self.frontier.add(c, 0):
                added += 1
        self.stats.sitemap = added
        log.info("sitemaps: %d url(s) found, %d enqueued", len(urls), added)

    async def _seed_from_feeds(self) -> None:
        from . import feeds as fd
        feed_urls = list(self.cfg.scope.feed_urls)
        if not feed_urls:
            # Autodiscover from seed pages' <link rel=alternate> tags.
            for seed in self.cfg.seeds:
                c = canonicalize(seed)
                if not c:
                    continue
                r = await self.fetcher.fetch(c)
                if r and not r.error and r.body:
                    feed_urls += fd.feeds_from_html(r.body, c)
        added = 0
        seen_feeds = set()
        for feed_url in feed_urls:
            if feed_url in seen_feeds:
                continue
            seen_feeds.add(feed_url)
            r = await self.fetcher.fetch(feed_url)
            if r is None or r.error or not r.body:
                continue
            for item in fd.parse_feed(r.body):
                link = canonicalize(item.get("link", ""))
                if link and await self.frontier.add(link, 0):
                    added += 1
        self.stats.feed += added
        log.info("feeds: %d feed(s), %d new item(s) enqueued", len(seen_feeds), added)

    async def teardown(self) -> None:
        await self.fetcher.aclose()
        if self.alerter is not None:
            await self.alerter.close()
        await self.frontier.close()
        await self.storage.close()

    def _install_signals(self) -> None:
        loop = asyncio.get_event_loop()
        for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
            if sig is None:
                continue
            try:
                loop.add_signal_handler(sig, self.stop.set)
            except (NotImplementedError, RuntimeError):
                pass  # Windows / non-main thread: KeyboardInterrupt handled in run()

    # ---- main run --------------------------------------------------------
    async def run(self) -> Stats:
        await self.setup()
        self._install_signals()
        try:
            if self.cfg.monitor.enabled:
                await self._run_monitor()
            else:
                await self._run_once()
        except KeyboardInterrupt:  # pragma: no cover
            self.stop.set()
        finally:
            await self.teardown()
        return self.stats

    async def _run_once(self) -> None:
        workers = self.cfg.effective_workers()
        # Per-pass done event for the status loop — NOT the global stop, so a
        # completed pass doesn't end monitor mode.
        run_done = asyncio.Event()
        tasks: List[asyncio.Task] = [
            asyncio.ensure_future(self._worker(i)) for i in range(workers)
        ]
        status_task = asyncio.ensure_future(self._status_loop(run_done))
        await asyncio.gather(*tasks, return_exceptions=True)
        run_done.set()
        await asyncio.gather(status_task, return_exceptions=True)
        # Send any buffered digest for this pass.
        if self.alerter is not None:
            await self.alerter.flush(self.cfg.name)

    async def _run_monitor(self) -> None:
        passes = 0
        while not self.stop.is_set():
            passes += 1
            log.info("monitor pass %d", passes)
            await self.storage.reset_for_recrawl()
            if not self.shared:
                self.frontier.clear()   # drop stale in-memory state (incl. setup's seeds)
                for url, depth in await self.storage.load_pending():
                    self.frontier.add_loaded(url, depth)
                # Fresh per-pass budget: everything is pending again.
                self.frontier.set_baseline(0)
                self.frontier.reset_dispatched()
                # Re-read feeds so newly published items are picked up each pass.
                if self.cfg.scope.use_feeds:
                    await self._seed_from_feeds()
            await self._run_once()
            if self.cfg.monitor.max_passes and passes >= self.cfg.monitor.max_passes:
                break
            if self.stop.is_set():
                break
            log.info("sleeping %.0fs until next pass", self.cfg.monitor.recrawl_interval)
            try:
                await asyncio.wait_for(self.stop.wait(), self.cfg.monitor.recrawl_interval)
            except asyncio.TimeoutError:
                pass

    async def _worker(self, wid: int) -> None:
        idle_spins = 0
        while not self.stop.is_set():
            item = await self.frontier.next()
            if item is None:
                # Stop when the max_pages cap is reached, or when the whole
                # frontier (this process + any others) is empty. Otherwise wait
                # — more URLs may be enqueued by in-flight fetches or freed by a
                # host cooldown / lease expiry.
                if self.frontier.capped or await self.frontier.remaining() == 0:
                    break
                idle_spins += 1
                await asyncio.sleep(0.02 if idle_spins < 50 else 0.15)
                continue
            idle_spins = 0
            url, depth, host = item
            state, blocked = "error", False
            try:
                state, blocked = await self._process(url, depth, host, wid)
            except Exception as exc:  # never let one URL kill a worker
                log.debug("worker %d error on %s: %s", wid, url, exc)
                self.stats.errors += 1
                state, blocked = "error", False
            finally:
                await self.frontier.complete(host, url, state, blocked=blocked)

    # ---- per-URL processing ---------------------------------------------
    async def _process(self, url: str, depth: int, host: str, wid: int) -> Tuple[str, bool]:
        if not await self.robots.allowed(url):
            self.stats.skipped += 1
            return ("done", False)  # mark done so it is not retried
        await self.frontier.note_crawl_delay(host, self.robots.crawl_delay(url))

        # Conditional GET: if we've seen this URL, ask the server whether it changed.
        extra = None
        if self.cfg.throttle.conditional_get:
            etag, lastmod = await self.storage.get_conditional(url)
            if etag or lastmod:
                extra = {}
                if etag:
                    extra["If-None-Match"] = etag
                if lastmod:
                    extra["If-Modified-Since"] = lastmod

        res = await self._fetch_with_retries(url, wid, extra_headers=extra)
        if res is None or res.error:
            self.stats.errors += 1
            return ("error", bool(res and res.blocked))
        if res.status == 304:
            self.stats.unchanged += 1     # not modified — keep the existing record
            return ("done", False)

        body = res.body
        kind = documents.sniff_kind(res.content_type, body)

        record = {
            "url": url, "final_url": res.final_url or url, "host": host,
            "depth": depth, "status": res.status, "transport": res.transport,
            "content_type": res.content_type, "size": len(body),
            "fetched_at": time.time(), "elapsed_ms": res.elapsed_ms,
            "title": "", "lang": None, "content_hash": None, "simhash": None,
            "matches": [], "extracted": {},
        }
        # Capture validators for next time's conditional GET.
        _h = {k.lower(): v for k, v in (res.headers or {}).items()}
        record["etag"] = _h.get("etag")
        record["last_modified"] = _h.get("last-modified")

        content_text = ""
        links: List[str] = []

        if kind == "html" and body:
            # Optional JS rendering.
            if self.renderer is not None and self._should_render(body):
                rendered = await self._render(url)
                if rendered:
                    body = rendered
            rec2, links, content_text = self.extractor.extract(
                res.final_url or url, body, res.content_type)
            record.update(rec2)
        elif kind in ("pdf", "text") and self.cfg.extract.extract_documents and body:
            # PDFs / text files become searchable, watchlist-scannable content.
            title, content_text = documents.extract_text(kind, body, res.content_type)
            record.update(self.extractor.finalize(title, content_text))
        # kind == "other" (images, binaries) -> metadata only.

        # Anchor text per link (transient; not persisted) for focus scoring.
        link_anchors = record.pop("_anchors", {}) or {}

        # Dedup + watchlist accounting (common to HTML and documents).
        duplicate = False
        ch = record.get("content_hash")
        sh = record.get("simhash") or 0
        if ch and await self.storage.content_hash_exists(ch):
            duplicate = True
        elif sh and not self.dedup.add_if_new(sh):
            duplicate = True
        if record.get("matches"):
            self.stats.matches += len(record["matches"])

        # Change detection + diff + alerting — read prior BEFORE save overwrites it.
        new_hash = record.get("content_hash")
        if new_hash and (self.alerter is not None or self.cfg.output.store_content):
            prior_hash, prior_content = await self.storage.get_prior(url)
            change = ("new" if prior_hash is None
                      else "unchanged" if prior_hash == new_hash else "changed")
            diff = None
            if change == "changed" and self.cfg.output.store_content:
                from .diffutil import summarize_diff
                diff = summarize_diff(prior_content or "", content_text or "")
                await self.storage.add_change(url, diff["added"], diff["removed"],
                                              diff["added_sample"])
                self.stats.changed += 1
            if self.alerter is not None and self.alerter.should_alert(change, record.get("matches")):
                cooled = False
                if self.cfg.alert.cooldown > 0:
                    last = await self.storage.last_alert_time(url)
                    cooled = last is not None and (time.time() - last) < self.cfg.alert.cooldown
                if not cooled:
                    snippet = ((content_text or "")[:self.cfg.alert.snippet_chars]
                               if self.cfg.alert.include_snippet else "")
                    await self.storage.add_alert(url, change, record.get("matches"),
                                                 record.get("title"), snippet)
                    self.stats.alerts += 1
                    payload = {"job": self.cfg.name, "url": url, "kind": change,
                               "title": record.get("title"), "matches": record.get("matches"),
                               "snippet": snippet, "ts": time.time()}
                    if diff and self.cfg.alert.include_diff:
                        payload.update({"added_lines": diff["added"],
                                        "removed_lines": diff["removed"],
                                        "added_sample": diff["added_sample"]})
                    await self.alerter.deliver(payload)

        headers = res.headers if "warc" in self.cfg.output.formats else None
        await self.storage.save_page(record, content_text=content_text,
                                     raw_bytes=body if ("warc" in self.cfg.output.formats
                                                        or self.cfg.output.save_raw_html) else None,
                                     headers=headers,
                                     store_content=self.cfg.output.store_content)
        self.stats.pages += 1
        self.stats.bytes += len(res.body)
        if duplicate:
            self.stats.dupes += 1

        if kind == "html" and self.cfg.extract.follow_links and not duplicate and links:
            await self.storage.add_links(url, links)
            # Links off a page that hit the watchlist inherit a relevance boost.
            parent_bonus = (int(self.cfg.focus.parent_bonus)
                            if (self.scorer and record.get("matches")) else 0)
            for link in links:
                priority = 0
                if self.scorer is not None:
                    priority = self.scorer.score(link_anchors.get(link, ""), link) + parent_bonus
                await self.frontier.add(link, depth + 1, priority=priority)

        return ("done", res.blocked)

    async def _fetch_with_retries(self, url: str, wid: int,
                                  extra_headers: Optional[dict] = None) -> Optional[FetchResult]:
        attempts = max(1, self.cfg.throttle.max_retries)
        backoff = 1.0
        # Default identity: a stable slot shared across workers, so the crawl
        # uses up to num_circuits parallel circuits (respecting num_circuits).
        n = max(1, self.cfg.tor.num_circuits)
        identity = "w{}".format(wid % n)
        ephemeral: List[str] = []  # fresh rotation identities to release afterward
        last: Optional[FetchResult] = None
        try:
            for attempt in range(attempts):
                res = await self.fetcher.fetch(url, tor_circuit=identity,
                                               extra_headers=extra_headers)
                last = res
                if res.ok:
                    return res
                if res.error is None and not res.blocked:
                    return res  # a real 404/410 etc. — no point retrying
                if attempt < attempts - 1 and not self.stop.is_set():
                    # Rotate before the retry: new global circuits (NEWNYM) plus
                    # a brand-new SOCKS identity so this retry rides a fresh
                    # circuit regardless of num_circuits.
                    if res.blocked and self.tor is not None and self.cfg.tor.rotate_on_block:
                        await self.tor.new_circuit()
                        self._rot_seq += 1
                        identity = "r{}s{}".format(wid, self._rot_seq)
                        ephemeral.append(identity)
                    # Honor a server's Retry-After if present, else exp backoff.
                    wait = backoff
                    if self.cfg.throttle.respect_retry_after and res.blocked:
                        hh = {k.lower(): v for k, v in (res.headers or {}).items()}
                        ra = parse_retry_after(hh.get("retry-after"))
                        if ra is not None:
                            wait = min(self.cfg.throttle.max_retry_after, ra)
                    await asyncio.sleep(wait)
                    backoff = min(30.0, backoff * 2)
            return last
        finally:
            # Drop the stale rotation clients so their circuits are not reused.
            for label in ephemeral:
                await self.fetcher.drop_tor_client(label)

    def _should_render(self, body: bytes) -> bool:
        if not self.cfg.render.enabled:
            return False
        if not self.cfg.render.only_when_empty:
            return True
        # Heuristic: little visible text => likely JS-rendered.
        return len(body) < 2000 or body.lower().count(b"<script") > 20

    async def _render(self, url: str) -> Optional[bytes]:
        try:
            html = await self.renderer.render(url)
            return html.encode("utf-8", "replace") if html else None
        except Exception as exc:  # pragma: no cover
            log.debug("render failed for %s: %s", url, exc)
            return None

    async def _status_loop(self, done_event) -> None:
        from .status import StatusReporter
        reporter = StatusReporter(self)
        # Stop the dashboard when the pass finishes OR a signal sets global stop.
        await reporter.run_until(done_event, self.stop)
