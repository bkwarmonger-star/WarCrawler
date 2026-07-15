"""Generate a self-contained crawl report (HTML or Markdown) from a crawl DB.

Read-only, synchronous stdlib sqlite3 — a one-shot summary of what a crawl or
monitor run collected: stats, per-host breakdown, watchlist hits, recent
alerts, and the latest structured records.
"""
from __future__ import annotations

import datetime
import html
import json
import sqlite3
from typing import Any, Dict


def _fmt_ts(ts) -> str:
    try:
        return datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def _human_bytes(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return "{:.1f}{}".format(n, unit)
        n /= 1024.0
    return "{:.1f}PB".format(n)


def collect(db_path: str, limit: int = 20) -> Dict[str, Any]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        d: Dict[str, Any] = {}
        d["pages"] = con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        d["hosts"] = con.execute("SELECT COUNT(DISTINCT host) FROM pages").fetchone()[0]
        d["bytes"] = con.execute("SELECT COALESCE(SUM(size),0) FROM pages").fetchone()[0]
        d["by_status"] = [(r[0], r[1]) for r in con.execute(
            "SELECT status, COUNT(*) FROM pages GROUP BY status ORDER BY 2 DESC")]
        d["by_transport"] = [(r[0], r[1]) for r in con.execute(
            "SELECT transport, COUNT(*) FROM pages GROUP BY transport ORDER BY 2 DESC")]
        d["top_hosts"] = [(r[0], r[1]) for r in con.execute(
            "SELECT host, COUNT(*) FROM pages GROUP BY host ORDER BY 2 DESC LIMIT ?", (limit,))]

        d["hit_count"] = con.execute(
            "SELECT COUNT(*) FROM pages WHERE matches IS NOT NULL AND matches!='[]'").fetchone()[0]
        d["hits"] = []
        for r in con.execute("SELECT url,title,matches FROM pages "
                             "WHERE matches IS NOT NULL AND matches!='[]' LIMIT ?", (limit,)):
            try:
                m = json.loads(r["matches"])
            except Exception:
                m = []
            d["hits"].append({"url": r["url"], "title": r["title"] or "", "matches": m})

        d["alert_count"] = 0
        d["alerts"] = []
        try:
            d["alert_count"] = con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
            for r in con.execute("SELECT created_at,kind,url,title FROM alerts "
                                 "ORDER BY created_at DESC LIMIT ?", (limit,)):
                d["alerts"].append({"ts": r["created_at"], "kind": r["kind"],
                                    "url": r["url"], "title": r["title"] or ""})
        except sqlite3.OperationalError:
            pass  # older DB without alerts table

        d["changes"] = []
        try:
            for r in con.execute("SELECT created_at,url,added,removed,sample FROM changes "
                                 "ORDER BY created_at DESC LIMIT ?", (limit,)):
                d["changes"].append({"ts": r["created_at"], "url": r["url"],
                                     "added": r["added"], "removed": r["removed"],
                                     "sample": r["sample"] or ""})
        except sqlite3.OperationalError:
            pass

        records = []
        try:
            for r in con.execute("SELECT url,structured FROM pages "
                                 "WHERE structured IS NOT NULL AND structured!='{}' LIMIT 500"):
                try:
                    s = json.loads(r["structured"])
                except Exception:
                    continue
                summ = s.get("summary") or {}
                if summ:
                    records.append({"url": r["url"],
                                    "title": summ.get("title") or "",
                                    "author": summ.get("author") or "",
                                    "published": summ.get("published") or "",
                                    "type": summ.get("type") or ""})
        except sqlite3.OperationalError:
            pass
        records.sort(key=lambda x: x.get("published") or "", reverse=True)
        d["records"] = records[:limit]

        d["recent"] = [{"url": r["url"], "title": r["title"] or "", "status": r["status"],
                        "ts": r["fetched_at"]}
                       for r in con.execute("SELECT url,title,status,fetched_at FROM pages "
                                            "ORDER BY fetched_at DESC LIMIT ?", (limit,))]
        return d
    finally:
        con.close()


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def build(db_path: str, job: str, fmt: str = "html", limit: int = 20) -> str:
    data = collect(db_path, limit=limit)
    return render_md(job, data) if fmt == "md" else render_html(job, data)


def render_md(job: str, d: Dict[str, Any]) -> str:
    L = ["# warcrawler report — {}".format(job),
         "", "_generated {}_".format(_fmt_ts(datetime.datetime.now().timestamp())), "",
         "## Summary",
         "- pages: **{}**  ·  hosts: **{}**  ·  downloaded: **{}**".format(
             d["pages"], d["hosts"], _human_bytes(d["bytes"])),
         "- watchlist-hit pages: **{}**  ·  alerts: **{}**".format(d["hit_count"], d["alert_count"]),
         ""]
    if d["top_hosts"]:
        L += ["## Top hosts", ""]
        L += ["- {} — {}".format(h, c) for h, c in d["top_hosts"]]
        L += [""]
    if d["hits"]:
        L += ["## Watchlist hits", ""]
        for h in d["hits"]:
            pats = ", ".join("{}×{}".format(m.get("pattern"), m.get("count")) for m in h["matches"])
            L.append("- [{}]({}) — {}".format(h["title"] or h["url"], h["url"], pats))
        L += [""]
    if d["alerts"]:
        L += ["## Recent alerts", ""]
        L += ["- `{}` **{}** {}".format(_fmt_ts(a["ts"]), a["kind"], a["url"]) for a in d["alerts"]]
        L += [""]
    if d.get("changes"):
        L += ["## Recent changes", ""]
        for c in d["changes"]:
            L.append("- `{}` +{}/-{} {} — {}".format(
                _fmt_ts(c["ts"]), c["added"], c["removed"], c["url"], c["sample"]))
        L += [""]
    if d["records"]:
        L += ["## Latest records", ""]
        for r in d["records"]:
            meta = " · ".join(x for x in (r["author"], r["published"], r["type"]) if x)
            L.append("- [{}]({}) {}".format(r["title"] or r["url"], r["url"],
                                            ("— " + meta) if meta else ""))
        L += [""]
    return "\n".join(L)


def _rows(rows):
    return "\n".join(rows)


def render_html(job: str, d: Dict[str, Any]) -> str:
    e = html.escape

    def hit_rows():
        out = []
        for h in d["hits"]:
            pats = ", ".join("{}&nbsp;×{}".format(e(str(m.get("pattern"))), m.get("count", 0))
                             for m in h["matches"])
            sample = ""
            for m in h["matches"]:
                if m.get("samples"):
                    sample = e(str(m["samples"][0]))[:160]
                    break
            out.append(
                "<tr><td><a href='{u}' target='_blank' rel='noopener noreferrer'>{t}</a>"
                "<div class='u'>{u}</div></td><td>{p}</td><td class='s'>{s}</td></tr>".format(
                    u=e(h["url"]), t=e(h["title"] or h["url"]), p=pats, s=sample))
        return _rows(out) or "<tr><td colspan=3 class='muted'>none</td></tr>"

    def alert_rows():
        out = []
        for a in d["alerts"]:
            out.append("<tr><td class='mono'>{}</td><td><span class='pill'>{}</span></td>"
                       "<td><a href='{u}' target='_blank' rel='noopener noreferrer'>{u}</a></td></tr>".format(
                           _fmt_ts(a["ts"]), e(a["kind"]), u=e(a["url"])))
        return _rows(out) or "<tr><td colspan=3 class='muted'>none</td></tr>"

    def change_rows():
        out = []
        for c in d.get("changes", []):
            out.append("<tr><td class='mono'>{}</td><td class='mono'>+{}/-{}</td>"
                       "<td><a href='{u}' target='_blank' rel='noopener noreferrer'>{u}</a>"
                       "<div class='s'>{s}</div></td></tr>".format(
                           _fmt_ts(c["ts"]), c["added"], c["removed"],
                           u=e(c["url"]), s=e(c["sample"])))
        return _rows(out) or "<tr><td colspan=3 class='muted'>none</td></tr>"

    def record_rows():
        out = []
        for r in d["records"]:
            meta = " · ".join(e(x) for x in (r["author"], r["published"], r["type"]) if x)
            out.append("<tr><td><a href='{u}' target='_blank' rel='noopener noreferrer'>{t}</a>"
                       "<div class='u'>{u}</div></td><td class='s'>{m}</td></tr>".format(
                           u=e(r["url"]), t=e(r["title"] or r["url"]), m=meta))
        return _rows(out) or "<tr><td colspan=2 class='muted'>none</td></tr>"

    def host_rows():
        return _rows("<tr><td>{}</td><td class='mono'>{}</td></tr>".format(e(h), c)
                     for h, c in d["top_hosts"]) or "<tr><td colspan=2 class='muted'>none</td></tr>"

    status = " · ".join("{}: {}".format(s, c) for s, c in d["by_status"])
    transport = " · ".join("{}: {}".format(t, c) for t, c in d["by_transport"])

    return """<!DOCTYPE html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>warcrawler report — {job}</title>
<style>
 :root{{--bg:#0a0c10;--card:#12161f;--bd:#242c3a;--tx:#d7dce6;--mut:#8b95a8;--ac:#ff8a3d;--ac2:#a98bff}}
 *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--tx);
  font-family:Inter,system-ui,-apple-system,sans-serif;line-height:1.5}}
 .wrap{{max-width:960px;margin:0 auto;padding:40px 6%}}
 h1{{font-family:ui-monospace,monospace;font-size:1.7rem;margin:0 0 4px}} h1 b{{color:var(--ac)}}
 .gen{{color:var(--mut);font-size:.85rem;margin-bottom:26px;font-family:ui-monospace,monospace}}
 h2{{font-size:1.05rem;margin:30px 0 10px;border-bottom:1px solid var(--bd);padding-bottom:6px}}
 .stats{{display:flex;flex-wrap:wrap;gap:10px}}
 .stat{{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:12px 16px;min-width:120px}}
 .stat .n{{font-size:1.5rem;font-weight:700;font-family:ui-monospace,monospace}}
 .stat .l{{color:var(--mut);font-size:.75rem;text-transform:uppercase;letter-spacing:.08em}}
 .meta{{color:var(--mut);font-size:.85rem;margin-top:8px;font-family:ui-monospace,monospace}}
 table{{width:100%;border-collapse:collapse;font-size:.9rem}}
 td{{padding:9px 10px;border-bottom:1px solid var(--bd);vertical-align:top}}
 a{{color:var(--ac)}} .u{{color:var(--mut);font-size:.72rem;font-family:ui-monospace,monospace;word-break:break-all}}
 .s{{color:var(--mut);font-size:.82rem}} .mono{{font-family:ui-monospace,monospace;color:var(--mut);font-size:.8rem}}
 .muted{{color:var(--mut)}} .pill{{background:#2a2440;color:var(--ac2);border-radius:6px;padding:2px 8px;font-size:.72rem;font-family:ui-monospace,monospace}}
</style></head><body><div class=wrap>
<h1><b>war</b>crawler report — {job}</h1>
<div class=gen>generated {gen}</div>
<div class=stats>
 <div class=stat><div class=n>{pages}</div><div class=l>pages</div></div>
 <div class=stat><div class=n>{hosts}</div><div class=l>hosts</div></div>
 <div class=stat><div class=n>{bytes}</div><div class=l>downloaded</div></div>
 <div class=stat><div class=n>{hitc}</div><div class=l>watchlist hits</div></div>
 <div class=stat><div class=n>{alertc}</div><div class=l>alerts</div></div>
</div>
<div class=meta>status — {status}<br>transport — {transport}</div>
<h2>Watchlist hits</h2><table>{hits}</table>
<h2>Recent alerts</h2><table>{alerts}</table>
<h2>Recent changes</h2><table>{changes}</table>
<h2>Latest records</h2><table>{records}</table>
<h2>Top hosts</h2><table>{hostsr}</table>
</div></body></html>""".format(
        job=e(job), gen=_fmt_ts(datetime.datetime.now().timestamp()),
        pages=d["pages"], hosts=d["hosts"], bytes=_human_bytes(d["bytes"]),
        hitc=d["hit_count"], alertc=d["alert_count"],
        status=e(status) or "n/a", transport=e(transport) or "n/a",
        hits=hit_rows(), alerts=alert_rows(), changes=change_rows(),
        records=record_rows(), hostsr=host_rows())
