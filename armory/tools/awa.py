#!/usr/bin/env python3
"""
Active Web Assessment (AWA) — Armory skill.

Actively assesses in-scope web targets: a native HTTP probe (status, server,
title, tech hints, security-header posture) and a scope-guarded Nuclei runner
for templated vulnerability scanning. Output normalizes to the canonical
findings schema and Nuclei JSONL (consumed by the Findings Parser).

SAFETY: This tool CONTACTS targets, so it is RoE-gated. Every target is checked
against the engagement scope allowlist BEFORE any request is sent; out-of-scope
targets are refused without contact. Runs in-sandbox over HTTP/HTTPS.

Usage:
    python3 awa.py probe --scope scope.json TARGET [TARGET ...] [--format table|json]
    python3 awa.py scan  --scope scope.json TARGET [TARGET ...] [--out nuclei.jsonl]
    python3 awa.py --self-test

scope.json:
    {"engagement_id":"ENG-042",
     "in_scope":{"domains":["app.client.com"],"cidrs":["203.0.113.0/24"],"ips":[]},
     "out_of_scope":["admin.client.com"]}
"""
import sys, os, re, json, ssl, argparse, datetime, ipaddress, shutil, subprocess, tempfile
import urllib.request, urllib.parse, urllib.error

UA = "Armory-AWA/1.0 (authorized security assessment)"
SEC_HEADERS = ["Strict-Transport-Security", "Content-Security-Policy", "X-Frame-Options",
               "X-Content-Type-Options", "Referrer-Policy", "Permissions-Policy"]
HEADER_SEV = {"Strict-Transport-Security": "MEDIUM", "Content-Security-Policy": "MEDIUM",
              "X-Frame-Options": "LOW", "X-Content-Type-Options": "LOW",
              "Referrer-Policy": "LOW", "Permissions-Policy": "LOW"}
PRIORITY_MATRIX = {
    "CRITICAL": {"internet_facing": "CRITICAL", "internal": "CRITICAL", "isolated": "HIGH"},
    "HIGH": {"internet_facing": "CRITICAL", "internal": "HIGH", "isolated": "MEDIUM"},
    "MEDIUM": {"internet_facing": "HIGH", "internal": "MEDIUM", "isolated": "MEDIUM"},
    "LOW": {"internet_facing": "MEDIUM", "internal": "LOW", "isolated": "LOW"},
    "INFO": {"internet_facing": "LOW", "internal": "LOW", "isolated": "LOW"},
}


class ScopeError(Exception):
    pass


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def host_of(target):
    t = target.strip()
    if "://" in t:
        t = urllib.parse.urlparse(t).netloc
    return t.split("@")[-1].split(":")[0].lower().rstrip(".")


class Scope:
    """Engagement authorization scope — the RoE gate, as code."""
    def __init__(self, data):
        insc = data.get("in_scope", data)
        self.engagement_id = data.get("engagement_id", "ENG")
        self.domains = [d.lower().lstrip("*.").rstrip(".") for d in insc.get("domains", [])]
        self.ips = set(insc.get("ips", []))
        self.cidrs = [ipaddress.ip_network(c, strict=False) for c in insc.get("cidrs", [])]
        self.out = [d.lower().lstrip("*.").rstrip(".") for d in (data.get("out_of_scope") or insc.get("out_of_scope") or [])]

    def in_scope(self, target):
        h = host_of(target)
        for d in self.out:                       # explicit exclusions win
            if h == d or h.endswith("." + d):
                return False
        try:
            ip = ipaddress.ip_address(h)
            return h in self.ips or any(ip in c for c in self.cidrs)
        except ValueError:
            pass
        return any(h == d or h.endswith("." + d) for d in self.domains)

    def guard(self, target):
        if not self.in_scope(target):
            raise ScopeError(f"{target} is not in engagement {self.engagement_id} scope — refused")


def _title(body):
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:120] if m else None


def _fetch(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        body = r.read(8192).decode("utf-8", "ignore")
        return {"status": getattr(r, "status", r.getcode()), "final_url": r.geturl(),
                "server": r.headers.get("Server"), "title": _title(body),
                "headers": {h: r.headers.get(h) for h in SEC_HEADERS if r.headers.get(h)}}


def probe(target, timeout=12):
    """Native HTTP probe (httpx-equivalent) — runs in-sandbox over HTTPS."""
    host = host_of(target)
    base = target if "://" in target else None
    tries = [base] if base else [f"https://{host}", f"http://{host}"]
    last = None
    for u in tries:
        try:
            r = _fetch(u, timeout)
            r["target"] = target
            r["scheme"] = "https" if u.startswith("https") else "http"
            r["missing_security_headers"] = [h for h in SEC_HEADERS if h not in r["headers"]]
            return r
        except Exception as e:
            last = str(e)
    return {"target": target, "error": last}


def mk_finding(engagement, title, severity, asset, exposure, evidence, remediation, cwe=None, wstg=None):
    priority = PRIORITY_MATRIX.get(severity, {}).get(exposure, "LOW")
    return {"engagement_id": engagement, "title": title, "category": "web_application",
            "asset": asset, "cwe": cwe or [], "owasp_wstg": wstg, "cve": None, "vie": None,
            "severity": severity, "exposure": exposure, "priority": priority,
            "evidence": evidence, "remediation": remediation,
            "status": "new", "source_tool": "awa", "discovered_at": now_iso(),
            "retest": {"status": "pending"}}


def findings_from_probe(engagement, r, exposure):
    out = []
    if r.get("error") or "missing_security_headers" not in r:
        return out
    for h in r["missing_security_headers"]:
        out.append(mk_finding(
            engagement, f"Missing security header: {h}", HEADER_SEV[h],
            {"url": r.get("final_url") or r["target"], "in_scope": True}, exposure,
            {"missing_header": h, "server": r.get("server"), "observed_at": r.get("final_url")},
            {"summary": f"Set the {h} response header per OWASP Secure Headers guidance."},
            cwe=["CWE-693"], wstg="WSTG-CONF-07"))
    return out


def run_nuclei(targets, scope, severities="low,medium,high,critical", timeout=240):
    """Scope-guarded Nuclei runner. Emits JSONL (consumed by the Findings Parser)."""
    inscope, refused = [], []
    for t in targets:
        (inscope if scope.in_scope(t) else refused).append(t)
    if not shutil.which("nuclei"):
        return {"available": False, "ran": False, "refused": refused, "jsonl": [],
                "message": "nuclei binary not on PATH — install ProjectDiscovery nuclei to enable templated scanning"}
    if not inscope:
        return {"available": True, "ran": False, "refused": refused, "jsonl": [],
                "message": "no in-scope targets"}
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("\n".join(inscope))
        path = f.name
    try:
        cmd = ["nuclei", "-l", path, "-jsonl", "-silent", "-severity", severities]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        lines = [ln for ln in res.stdout.splitlines() if ln.strip().startswith("{")]
        return {"available": True, "ran": True, "refused": refused, "count": len(lines), "jsonl": lines}
    except subprocess.TimeoutExpired:
        return {"available": True, "ran": False, "refused": refused, "jsonl": [], "message": "nuclei timed out"}
    finally:
        os.unlink(path)


def load_scope(path):
    return Scope(json.loads(open(path).read()))


def cmd_probe(scope, targets, exposure):
    results, findings, refused = [], [], []
    for t in targets:
        if not scope.in_scope(t):
            refused.append(t)
            continue
        r = probe(t)
        results.append(r)
        findings += findings_from_probe(scope.engagement_id, r, exposure)
    return {"engagement": scope.engagement_id, "probed": results, "refused": refused, "findings": findings}


def print_probe(out):
    for t in out["refused"]:
        print(f"  REFUSED (out of scope, not contacted): {t}")
    for r in out["probed"]:
        if r.get("error"):
            print(f"  {r['target']}: ERROR {r['error']}")
            continue
        print(f"  {r['target']} [{r.get('scheme')}] {r.get('status')} "
              f"server={r.get('server') or '-'} title={r.get('title') or '-'}")
        miss = r.get("missing_security_headers") or []
        print(f"      missing headers: {', '.join(miss) if miss else 'none'}")
    print(f"\n  {len(out['findings'])} finding(s) · {len(out['refused'])} refused · engagement {out['engagement']}")


def self_test():
    sc = Scope({"engagement_id": "T", "in_scope": {"domains": ["example.com"], "cidrs": ["10.0.0.0/24"]},
                "out_of_scope": ["secret.example.com"]})
    assert sc.in_scope("example.com")
    assert sc.in_scope("https://sub.example.com/path")
    assert sc.in_scope("10.0.0.5")
    assert not sc.in_scope("10.0.1.5")
    assert not sc.in_scope("evil.com")
    assert not sc.in_scope("secret.example.com"), "explicit out-of-scope must win"
    raised = False
    try:
        sc.guard("evil.com")
    except ScopeError:
        raised = True
    assert raised, "guard must raise on out-of-scope"
    # nuclei wrapper refuses out-of-scope even when binary absent
    nres = run_nuclei(["evil.com"], sc)
    assert "evil.com" in nres["refused"]
    print("SELF-TEST PASSED — scope-guard enforces in/out scope (domains, wildcards, CIDR, "
          "explicit exclusions); guard raises; nuclei wrapper scope-filters. "
          f"nuclei available: {bool(shutil.which('nuclei'))}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Active Web Assessment")
    ap.add_argument("--self-test", action="store_true")
    sub = ap.add_subparsers(dest="command")
    for name in ("probe", "scan"):
        sp = sub.add_parser(name)
        sp.add_argument("targets", nargs="+")
        sp.add_argument("--scope", required=True, help="scope JSON file")
        sp.add_argument("--exposure", default="internet_facing",
                        choices=["internet_facing", "internal", "isolated"])
        sp.add_argument("--format", default="table", choices=["table", "json"])
        sp.add_argument("--out", help="write nuclei JSONL here (scan)")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.command:
        ap.error("choose a command: probe or scan (or --self-test)")
    scope = load_scope(a.scope)
    if a.command == "probe":
        out = cmd_probe(scope, a.targets, a.exposure)
        print(json.dumps(out, indent=2) if a.format == "json" else "")
        if a.format == "table":
            print_probe(out)
    else:
        res = run_nuclei(a.targets, scope)
        if a.out and res.get("jsonl"):
            open(a.out, "w").write("\n".join(res["jsonl"]))
        summary = {k: v for k, v in res.items() if k != "jsonl"}
        print(json.dumps(summary, indent=2))
        if a.out and res.get("jsonl"):
            print(f"wrote {len(res['jsonl'])} nuclei findings to {a.out} (feed to the Findings Parser)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
