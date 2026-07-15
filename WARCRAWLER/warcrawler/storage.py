"""Persistent storage: resumable SQLite state, FTS5 search, WARC + JSONL output.

All state lives under a single data directory so the whole crawl is portable
and survives the USB stick being unplugged mid-run.
"""
from __future__ import annotations

import gzip
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from .logutil import get_logger

log = get_logger()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS frontier (
    url        TEXT PRIMARY KEY,
    host       TEXT,
    depth      INTEGER NOT NULL DEFAULT 0,
    priority   INTEGER NOT NULL DEFAULT 0,
    state      TEXT NOT NULL DEFAULT 'pending',   -- pending|claimed|done|error
    retries    INTEGER NOT NULL DEFAULT 0,
    added_at   REAL,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_frontier_state ON frontier(state);

CREATE TABLE IF NOT EXISTS pages (
    url          TEXT PRIMARY KEY,
    final_url    TEXT,
    host         TEXT,
    depth        INTEGER,
    status       INTEGER,
    transport    TEXT,
    content_type TEXT,
    size         INTEGER,
    title        TEXT,
    lang         TEXT,
    content_hash TEXT,
    simhash      TEXT,      -- unsigned 64-bit stored as text; INTEGER affinity
                            -- would overflow values above 2^63 into a lossy REAL
    fetched_at   REAL,
    elapsed_ms   INTEGER,
    etag         TEXT,      -- for conditional GET (If-None-Match)
    last_modified TEXT,     -- for conditional GET (If-Modified-Since)
    matches      TEXT,      -- JSON list of watchlist hits
    extracted    TEXT,      -- JSON dict of css/xpath rule results
    structured   TEXT,      -- JSON: JSON-LD / OpenGraph / meta + normalized summary
    content      TEXT       -- latest extracted text (capped) for diffing/report
);
CREATE INDEX IF NOT EXISTS idx_pages_host ON pages(host);
CREATE INDEX IF NOT EXISTS idx_pages_hash ON pages(content_hash);

CREATE TABLE IF NOT EXISTS links (
    from_url TEXT,
    to_url   TEXT
);
CREATE INDEX IF NOT EXISTS idx_links_from ON links(from_url);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    url        TEXT,
    kind       TEXT,        -- new | changed
    title      TEXT,
    matches    TEXT,        -- JSON list of watchlist hits
    snippet    TEXT,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);

CREATE TABLE IF NOT EXISTS changes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    url        TEXT,
    added      INTEGER,
    removed    INTEGER,
    sample     TEXT,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_changes_created ON changes(created_at);
"""

_CONTENT_CAP = 500_000  # per-page stored text cap (bytes-ish) to bound DB growth


class Storage:
    def __init__(self, data_dir: Path, job_name: str,
                 formats: Optional[List[str]] = None, fts: bool = True,
                 save_raw_html: bool = False, network_fs: bool = False):
        self.data_dir = Path(data_dir)
        self.job_name = job_name
        self.formats = set(formats or ["sqlite", "jsonl"])
        self.want_fts = fts
        self.save_raw_html = save_raw_html
        self.network_fs = network_fs
        self.db_path = self.data_dir / "{}.sqlite".format(job_name)
        self._db: Optional[aiosqlite.Connection] = None
        self._fts_ok = False
        self._jsonl = None
        self._warc = None
        self._warc_writer = None

    async def open(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        await self._db.executescript(_SCHEMA)
        # WAL improves concurrent read/write and resumability. WAL does not work
        # over network filesystems, so fall back to DELETE journalling there.
        await self._db.execute(
            "PRAGMA journal_mode=DELETE;" if self.network_fs else "PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        # Wait for locks instead of failing instantly when other processes write.
        await self._db.execute("PRAGMA busy_timeout=15000;")
        # Migrate older DBs that predate the conditional-GET columns.
        for col in ("etag TEXT", "last_modified TEXT", "structured TEXT", "content TEXT"):
            try:
                await self._db.execute("ALTER TABLE pages ADD COLUMN {}".format(col))
            except Exception:
                pass  # column already exists
        if self.want_fts:
            try:
                await self._db.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts "
                    "USING fts5(url, title, content);"
                )
                self._fts_ok = True
            except Exception as exc:  # FTS5 not compiled in
                log.warning("FTS5 unavailable, full-text search disabled: %s", exc)
                self._fts_ok = False
        await self._db.commit()

        if "jsonl" in self.formats:
            path = self.data_dir / "{}.jsonl".format(self.job_name)
            self._jsonl = open(path, "a", encoding="utf-8")
        if "warc" in self.formats:
            self._open_warc()

    def _open_warc(self) -> None:
        try:
            from warcio.warcwriter import WARCWriter
        except Exception as exc:  # pragma: no cover
            log.warning("warcio not installed, WARC output disabled: %s", exc)
            return
        warc_dir = self.data_dir / "warc"
        warc_dir.mkdir(parents=True, exist_ok=True)
        path = warc_dir / "{}-{}.warc.gz".format(self.job_name, int(time.time()))
        self._warc = open(path, "wb")
        self._warc_writer = WARCWriter(self._warc, gzip=True)

    # ---- meta ------------------------------------------------------------
    async def set_meta(self, key: str, value: str) -> None:
        await self._db.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        await self._db.commit()

    async def get_meta(self, key: str) -> Optional[str]:
        async with self._db.execute("SELECT value FROM meta WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

    # ---- frontier --------------------------------------------------------
    async def add_frontier(self, url: str, host: str, depth: int,
                           priority: int = 0) -> bool:
        now = time.time()
        cur = await self._db.execute(
            "INSERT OR IGNORE INTO frontier(url,host,depth,priority,state,added_at,updated_at) "
            "VALUES(?,?,?,?, 'pending', ?, ?)", (url, host, depth, priority, now, now))
        await self._db.commit()
        return cur.rowcount > 0

    async def load_pending(self) -> List[Tuple[str, int]]:
        # Reset any rows left 'claimed' by a previous interrupted run.
        await self._db.execute(
            "UPDATE frontier SET state='pending' WHERE state='claimed'")
        await self._db.commit()
        rows: List[Tuple[str, int]] = []
        async with self._db.execute(
                "SELECT url, depth FROM frontier WHERE state='pending' "
                "ORDER BY priority DESC, added_at ASC") as cur:
            async for r in cur:
                rows.append((r[0], r[1]))
        return rows

    async def mark(self, url: str, state: str, retries: Optional[int] = None) -> None:
        if retries is None:
            await self._db.execute(
                "UPDATE frontier SET state=?, updated_at=? WHERE url=?",
                (state, time.time(), url))
        else:
            await self._db.execute(
                "UPDATE frontier SET state=?, retries=?, updated_at=? WHERE url=?",
                (state, retries, time.time(), url))
        await self._db.commit()

    async def reset_for_recrawl(self) -> None:
        await self._db.execute("UPDATE frontier SET state='pending'")
        await self._db.commit()

    # ---- dedup helpers ---------------------------------------------------
    async def content_hash_exists(self, content_hash: str) -> bool:
        async with self._db.execute(
                "SELECT 1 FROM pages WHERE content_hash=? LIMIT 1",
                (content_hash,)) as cur:
            return await cur.fetchone() is not None

    async def load_simhashes(self) -> List[int]:
        out: List[int] = []
        async with self._db.execute(
                "SELECT simhash FROM pages WHERE simhash IS NOT NULL") as cur:
            async for r in cur:
                out.append(int(r[0]))
        return out

    # ---- page storage ----------------------------------------------------
    async def save_page(self, rec: Dict[str, Any], content_text: str = "",
                        raw_bytes: Optional[bytes] = None,
                        headers: Optional[Dict[str, str]] = None,
                        store_content: bool = False) -> None:
        content_col = (content_text[:_CONTENT_CAP] if (store_content and content_text) else None)
        await self._db.execute(
            "INSERT OR REPLACE INTO pages(url,final_url,host,depth,status,transport,"
            "content_type,size,title,lang,content_hash,simhash,fetched_at,elapsed_ms,"
            "etag,last_modified,matches,extracted,structured,content) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rec.get("url"), rec.get("final_url"), rec.get("host"),
                rec.get("depth"), rec.get("status"), rec.get("transport"),
                rec.get("content_type"), rec.get("size"), rec.get("title"),
                rec.get("lang"), rec.get("content_hash"),
                # SimHash is unsigned 64-bit; store as text so it never
                # overflows SQLite's signed INTEGER.
                (str(rec["simhash"]) if rec.get("simhash") is not None else None),
                rec.get("fetched_at"), rec.get("elapsed_ms"),
                rec.get("etag"), rec.get("last_modified"),
                json.dumps(rec.get("matches") or []),
                json.dumps(rec.get("extracted") or {}),
                json.dumps(rec.get("structured") or {}),
                content_col,
            ))
        if self._fts_ok and content_text:
            # Replace any prior FTS row for this URL so recrawls don't duplicate.
            await self._db.execute("DELETE FROM pages_fts WHERE url=?", (rec.get("url"),))
            await self._db.execute(
                "INSERT INTO pages_fts(url,title,content) VALUES(?,?,?)",
                (rec.get("url"), rec.get("title") or "", content_text))
        await self._db.commit()

        if self._jsonl is not None:
            row = dict(rec)
            row["content"] = content_text
            self._jsonl.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._jsonl.flush()

        if self._warc_writer is not None and raw_bytes is not None:
            self._write_warc(rec.get("final_url") or rec.get("url"), raw_bytes, headers)

        if self.save_raw_html and raw_bytes is not None:
            self._write_raw_html(rec.get("url"), raw_bytes)

    def _write_warc(self, url: str, raw: bytes, headers: Optional[Dict[str, str]]):
        try:
            from warcio.statusandheaders import StatusAndHeaders
            import io
            hdr_list = list((headers or {}).items())
            http_headers = StatusAndHeaders("200 OK", hdr_list, protocol="HTTP/1.1")
            record = self._warc_writer.create_warc_record(
                url, "response", payload=io.BytesIO(raw),
                http_headers=http_headers)
            self._warc_writer.write_record(record)
        except Exception as exc:  # pragma: no cover
            log.debug("WARC write failed for %s: %s", url, exc)

    def _write_raw_html(self, url: str, raw: bytes):
        import hashlib as _h
        html_dir = self.data_dir / "html"
        html_dir.mkdir(parents=True, exist_ok=True)
        name = _h.blake2b(url.encode("utf-8"), digest_size=12).hexdigest()
        with gzip.open(html_dir / (name + ".html.gz"), "wb") as fh:
            fh.write(raw)

    async def add_links(self, from_url: str, to_urls: List[str]) -> None:
        if not to_urls:
            return
        await self._db.executemany(
            "INSERT INTO links(from_url,to_url) VALUES(?,?)",
            [(from_url, u) for u in to_urls])
        await self._db.commit()

    # ---- queries ---------------------------------------------------------
    async def get_prior_hash(self, url: str) -> Optional[str]:
        """The URL's previously stored content hash (read before overwrite)."""
        async with self._db.execute(
                "SELECT content_hash FROM pages WHERE url=?", (url,)) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def get_prior(self, url: str):
        """(content_hash, content) for a URL, read before it is overwritten."""
        async with self._db.execute(
                "SELECT content_hash, content FROM pages WHERE url=?", (url,)) as cur:
            row = await cur.fetchone()
        return (row[0], row[1]) if row else (None, None)

    async def add_change(self, url: str, added: int, removed: int, sample: str) -> None:
        await self._db.execute(
            "INSERT INTO changes(url,added,removed,sample,created_at) VALUES(?,?,?,?,?)",
            (url, added, removed, sample or "", time.time()))
        await self._db.commit()

    async def recent_changes(self, limit: int = 50):
        rows = []
        async with self._db.execute(
                "SELECT created_at,url,added,removed,sample FROM changes "
                "ORDER BY created_at DESC LIMIT ?", (limit,)) as cur:
            async for r in cur:
                rows.append((r[0], r[1], r[2], r[3], r[4]))
        return rows

    async def add_alert(self, url: str, kind: str, matches, title: str,
                        snippet: str) -> None:
        await self._db.execute(
            "INSERT INTO alerts(url,kind,title,matches,snippet,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (url, kind, title or "", json.dumps(matches or []), snippet or "",
             time.time()))
        await self._db.commit()

    async def last_alert_time(self, url: str):
        async with self._db.execute(
                "SELECT MAX(created_at) FROM alerts WHERE url=?", (url,)) as cur:
            row = await cur.fetchone()
        return row[0] if row and row[0] is not None else None

    async def recent_alerts(self, limit: int = 50):
        rows = []
        async with self._db.execute(
                "SELECT created_at,kind,url,title FROM alerts "
                "ORDER BY created_at DESC LIMIT ?", (limit,)) as cur:
            async for r in cur:
                rows.append((r[0], r[1], r[2], r[3]))
        return rows

    async def get_conditional(self, url: str):
        """Return (etag, last_modified) for a previously fetched URL, or (None, None)."""
        async with self._db.execute(
                "SELECT etag, last_modified FROM pages WHERE url=?", (url,)) as cur:
            row = await cur.fetchone()
        return (row[0], row[1]) if row else (None, None)

    async def count_pages(self) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM pages") as cur:
            return (await cur.fetchone())[0]

    async def stats(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        async with self._db.execute("SELECT COUNT(*) FROM pages") as cur:
            out["pages"] = (await cur.fetchone())[0]
        for state in ("pending", "claimed", "done", "error"):
            async with self._db.execute(
                    "SELECT COUNT(*) FROM frontier WHERE state=?", (state,)) as cur:
                out[state] = (await cur.fetchone())[0]
        return out

    async def search(self, query: str, limit: int = 20) -> List[Tuple[str, str]]:
        if not self._fts_ok:
            raise RuntimeError("FTS5 not available in this SQLite build")
        rows: List[Tuple[str, str]] = []
        async with self._db.execute(
                "SELECT url, title FROM pages_fts WHERE pages_fts MATCH ? LIMIT ?",
                (query, limit)) as cur:
            async for r in cur:
                rows.append((r[0], r[1]))
        return rows

    async def close(self) -> None:
        try:
            if self._jsonl is not None:
                self._jsonl.close()
            if self._warc is not None:
                self._warc.close()
        finally:
            if self._db is not None:
                await self._db.close()
