"""Job configuration: dataclasses, YAML loading, presets and CLI overrides."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
    _HAS_YAML = True
except Exception:  # pragma: no cover
    _HAS_YAML = False

from .presets import THROTTLE_PRESETS, DEFAULT_PROFILE, USER_AGENTS, HEADER_PROFILES


@dataclass
class ScopeConfig:
    max_depth: int = 3
    max_pages: int = 1000
    same_domain_only: bool = True
    allow_domains: List[str] = field(default_factory=list)
    deny_domains: List[str] = field(default_factory=list)
    include_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)
    allowed_schemes: List[str] = field(default_factory=lambda: ["http", "https"])
    strip_tracking: bool = True
    # Seed the frontier from sitemaps (robots.txt Sitemap: lines + /sitemap.xml,
    # following sitemap-index files) for coverage beyond link-following.
    use_sitemaps: bool = False
    sitemap_urls: List[str] = field(default_factory=list)   # explicit overrides discovery
    sitemap_max_urls: int = 5000
    # Seed the freshest items from RSS/Atom feeds (explicit or autodiscovered
    # from seeds). Re-read each monitor pass so new items are picked up.
    use_feeds: bool = False
    feed_urls: List[str] = field(default_factory=list)


@dataclass
class ThrottleConfig:
    profile: str = DEFAULT_PROFILE
    per_host_delay: float = 0.5
    per_host_concurrency: int = 2
    global_concurrency: int = 16
    respect_robots: bool = True
    adaptive_backoff: bool = True
    max_retries: int = 3
    timeout: float = 30.0
    jitter: float = 0.3  # +/- fraction applied to per_host_delay
    conditional_get: bool = True         # send If-None-Match / If-Modified-Since; skip 304s
    respect_retry_after: bool = True     # honor a server's Retry-After header
    max_retry_after: float = 120.0       # cap on how long we'll wait for Retry-After

    @classmethod
    def from_profile(cls, profile: str) -> "ThrottleConfig":
        preset = THROTTLE_PRESETS.get(profile, THROTTLE_PRESETS[DEFAULT_PROFILE])
        cfg = cls(profile=profile)
        for k, v in preset.items():
            setattr(cfg, k, v)
        return cfg


@dataclass
class StealthConfig:
    rotate_user_agent: bool = True
    user_agents: List[str] = field(default_factory=lambda: list(USER_AGENTS))
    header_profiles: List[Dict[str, str]] = field(default_factory=lambda: list(HEADER_PROFILES))
    # Static headers sent on EVERY request (override the rotating profile). Use
    # for auth: {"Authorization": "Bearer ...", "Cookie": "session=..."}.
    headers: Dict[str, str] = field(default_factory=dict)
    # Convenience cookie map -> assembled into a single Cookie header.
    cookies: Dict[str, str] = field(default_factory=dict)
    proxies: List[str] = field(default_factory=list)      # e.g. ["http://user:pass@1.2.3.4:8080"]
    proxy_rotation: str = "round_robin"                    # round_robin | random | sticky_host
    tls_impersonate: Optional[str] = None                  # e.g. "chrome" (requires curl_cffi)
    max_redirects: int = 10


@dataclass
class TorConfig:
    enabled: bool = False
    auto_start: bool = True
    tor_binary: Optional[str] = None       # path; default ./tor/<os>/tor(.exe)
    socks_port: int = 9050
    control_port: int = 9051
    control_password: Optional[str] = None
    data_dir: Optional[str] = None         # default <data>/tor
    num_circuits: int = 4                  # isolated circuits via SOCKS auth
    bootstrap_timeout: float = 120.0
    rotate_on_block: bool = True           # NEWNYM when a target starts blocking


@dataclass
class RenderConfig:
    enabled: bool = False
    engine: str = "playwright"             # playwright (chromium)
    wait_until: str = "networkidle"
    timeout: float = 45.0
    wait_ms: int = 0                       # extra settle time after load
    stealth: bool = True
    only_when_empty: bool = True           # render only if static fetch had ~no text


@dataclass
class ExtractConfig:
    extract_content: bool = True           # main-article text via trafilatura
    extract_documents: bool = True         # pull text from PDFs / text files too
    structured_data: bool = True           # JSON-LD / OpenGraph / meta records
    detect_language: bool = True
    css_rules: Dict[str, str] = field(default_factory=dict)     # name -> CSS selector
    xpath_rules: Dict[str, str] = field(default_factory=dict)   # name -> XPath
    watchlist: List[str] = field(default_factory=list)          # regex patterns
    watchlist_ignore_case: bool = True
    follow_links: bool = True
    max_content_bytes: int = 10 * 1024 * 1024


@dataclass
class OutputConfig:
    dir: Optional[str] = None              # default <data>
    formats: List[str] = field(default_factory=lambda: ["sqlite", "jsonl"])  # sqlite|jsonl|warc|html
    fts: bool = True                       # full-text index in SQLite
    save_raw_html: bool = False
    store_content: bool = True             # keep latest page text (enables diffs + report snippets)


@dataclass
class MonitorConfig:
    enabled: bool = False
    recrawl_interval: float = 3600.0       # seconds between recrawl passes
    max_passes: int = 0                    # 0 = unlimited


@dataclass
class LoginConfig:
    # Log in before crawling and reuse the session cookies for every request.
    enabled: bool = False
    url: str = ""                          # login endpoint
    method: str = "POST"                   # POST | GET
    data: Dict[str, str] = field(default_factory=dict)   # form fields (user/pass/...)
    json_body: bool = False                # send data as JSON instead of form-encoded
    csrf_url: Optional[str] = None         # page to GET first to obtain a CSRF token
    csrf_field: Optional[str] = None       # hidden <input name> holding the token
    csrf_regex: Optional[str] = None       # OR a regex (group 1) to extract the token
    csrf_post_field: Optional[str] = None  # field name to send the token as (default=csrf_field)
    success_contains: Optional[str] = None # confirm login by finding this in the response


@dataclass
class AlertConfig:
    # Push an alert when a new/changed page matches your interests. Great for
    # monitor mode; change detection keeps recurring passes from re-alerting.
    enabled: bool = False
    # what fires an alert: watchlist|new|changed. (Named 'triggers', not 'on',
    # because YAML parses a bare `on:` key as the boolean True.)
    triggers: List[str] = field(default_factory=lambda: ["watchlist"])
    webhook_url: Optional[str] = None       # POST a JSON payload here per alert
    webhook_timeout: float = 10.0
    include_snippet: bool = True
    snippet_chars: int = 300
    include_diff: bool = True               # attach added/removed lines to change alerts
    cooldown: float = 0.0                    # secs to suppress re-alerting the same URL (0=off)
    digest: bool = False                     # batch a pass's alerts into one webhook POST


@dataclass
class FocusConfig:
    # Crawl the most relevant URLs first (best-first), scored by keyword/pattern
    # matches in the link's anchor text and URL.
    enabled: bool = False
    keywords: List[str] = field(default_factory=list)   # plain phrases (case-insensitive)
    patterns: List[str] = field(default_factory=list)    # regex patterns
    use_watchlist: bool = True                           # also use extract.watchlist terms
    anchor_weight: float = 2.0
    url_weight: float = 1.0
    parent_bonus: float = 3.0    # boost links found on a page that hit the watchlist


@dataclass
class FleetConfig:
    # When True, use a shared frontier so multiple processes (and, with the
    # redis backend, multiple machines) can work-steal from one queue.
    shared: bool = False
    backend: str = "sqlite"                # sqlite | redis (only used when shared)
    redis_url: str = "redis://localhost:6379/0"
    namespace: Optional[str] = None        # redis key prefix; defaults to job name
    lease: float = 300.0                   # seconds before a stale claim is re-stealable
    network_fs: bool = False               # set True if the SQLite file lives on a
                                           # network share (disables WAL; slower, best-effort)


@dataclass
class JobConfig:
    name: str = "crawl"
    seeds: List[str] = field(default_factory=list)
    transport: str = "auto"                # auto | clearnet | tor
    workers: int = 0                       # 0 => use throttle.global_concurrency
    user_agent: Optional[str] = None       # pin a single UA (disables rotation)
    scope: ScopeConfig = field(default_factory=ScopeConfig)
    throttle: ThrottleConfig = field(default_factory=ThrottleConfig)
    stealth: StealthConfig = field(default_factory=StealthConfig)
    tor: TorConfig = field(default_factory=TorConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    extract: ExtractConfig = field(default_factory=ExtractConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    fleet: FleetConfig = field(default_factory=FleetConfig)
    focus: FocusConfig = field(default_factory=FocusConfig)
    alert: AlertConfig = field(default_factory=AlertConfig)
    login: LoginConfig = field(default_factory=LoginConfig)

    # ---- construction helpers -------------------------------------------
    @staticmethod
    def _merge(dc_instance, data: Dict[str, Any]):
        """Apply a dict of overrides onto a dataclass instance in place."""
        valid = {f.name for f in dataclasses.fields(dc_instance)}
        for key, value in (data or {}).items():
            if key not in valid:
                raise ValueError("unknown config key: {}.{}".format(
                    type(dc_instance).__name__, key))
            setattr(dc_instance, key, value)
        return dc_instance

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobConfig":
        data = dict(data or {})
        job = cls()
        # nested sections
        section_types = {
            "scope": ScopeConfig, "throttle": ThrottleConfig,
            "stealth": StealthConfig, "tor": TorConfig, "render": RenderConfig,
            "extract": ExtractConfig, "output": OutputConfig, "monitor": MonitorConfig,
            "fleet": FleetConfig, "focus": FocusConfig, "alert": AlertConfig,
            "login": LoginConfig,
        }
        # Apply throttle profile first so explicit keys can override it.
        throttle_data = dict(data.get("throttle") or {})
        profile = throttle_data.pop("profile", None)
        if profile:
            job.throttle = ThrottleConfig.from_profile(profile)
        cls._merge(job.throttle, throttle_data)

        for section, sect_type in section_types.items():
            if section == "throttle":
                continue
            if section in data:
                inst = getattr(job, section)
                cls._merge(inst, data.pop(section) or {})
        data.pop("throttle", None)

        # top-level scalars
        for key in ("name", "seeds", "transport", "workers", "user_agent"):
            if key in data:
                setattr(job, key, data.pop(key))
        if data:
            raise ValueError("unknown top-level config keys: {}".format(list(data)))
        job._validate()
        return job

    @classmethod
    def load(cls, path: str) -> "JobConfig":
        if not _HAS_YAML:
            raise RuntimeError("PyYAML is required to load job files. pip install pyyaml")
        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        return cls.from_dict(data)

    def apply_overrides(self, overrides: Dict[str, Any]) -> "JobConfig":
        """Apply CLI overrides (flat, dotted keys like 'throttle.respect_robots')."""
        for dotted, value in overrides.items():
            if value is None:
                continue
            if "." in dotted:
                section, key = dotted.split(".", 1)
                setattr(getattr(self, section), key, value)
            else:
                setattr(self, dotted, value)
        self._validate()
        return self

    def effective_workers(self) -> int:
        return self.workers if self.workers > 0 else int(self.throttle.global_concurrency)

    def _validate(self) -> None:
        if not self.seeds:
            # allowed at construction; the runner enforces before crawling
            pass
        if self.transport not in ("auto", "clearnet", "tor"):
            raise ValueError("transport must be auto|clearnet|tor")
        if self.stealth.proxy_rotation not in ("round_robin", "random", "sticky_host"):
            raise ValueError("stealth.proxy_rotation invalid")
        if self.fleet.backend not in ("sqlite", "redis"):
            raise ValueError("fleet.backend must be sqlite|redis")
        bad = set(self.alert.triggers) - {"watchlist", "new", "changed"}
        if bad:
            raise ValueError("alert.triggers must be a subset of watchlist|new|changed")
