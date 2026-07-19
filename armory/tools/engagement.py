#!/usr/bin/env python3
"""
Engagement & Scope Manager — Armory skill.

The authorization gate as data. Turns the Rules-of-Engagement template into a
machine-checkable authorization record, and exposes ONE decision every active
tool calls before touching a target:

    authorize_action(target, technique, at) -> {allowed: bool, reasons: [...]}

It enforces the four required RoE elements (signed authorization, in-scope list,
test window, permitted techniques), the test window, in-scope matching
(domains/wildcards/CIDR/IP + explicit exclusions), and permitted-vs-prohibited
techniques. No network access — pure governance logic.

Usage:
    python3 engagement.py new --id ENG-042 --client "Acme Corp" --out record.json
    python3 engagement.py validate record.json [--at 2026-07-13T12:00:00Z]
    python3 engagement.py check record.json --target app.acme.com --technique "web application testing"
    python3 engagement.py --self-test
"""
import sys, json, argparse, datetime, ipaddress, urllib.parse

REQUIRED = ["signed_authorization", "in_scope_list", "test_window", "permitted_techniques"]


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def host_of(target):
    t = target.strip()
    if "://" in t:
        t = urllib.parse.urlparse(t).netloc
    return t.split("@")[-1].split(":")[0].lower().rstrip(".")


class Engagement:
    def __init__(self, rec):
        self.rec = rec
        self.engagement_id = rec.get("engagement_id", "ENG")
        insc = rec.get("in_scope", {}) or {}
        self.domains = [d.lower().lstrip("*.").rstrip(".") for d in insc.get("domains", [])]
        self.ips = set(insc.get("ips", []))
        self.cidrs = [ipaddress.ip_network(c, strict=False) for c in insc.get("cidrs", [])]
        self.out = [d.lower().lstrip("*.").rstrip(".") for d in (rec.get("out_of_scope") or [])]
        self.permitted = [t.lower() for t in rec.get("permitted_techniques", [])]
        self.prohibited = [t.lower() for t in rec.get("prohibited_techniques", [])]

    # ---- scope ----
    def in_scope(self, target):
        h = host_of(target)
        for d in self.out:
            if h == d or h.endswith("." + d):
                return False
        try:
            ip = ipaddress.ip_address(h)
            return h in self.ips or any(ip in c for c in self.cidrs)
        except ValueError:
            pass
        return any(h == d or h.endswith("." + d) for d in self.domains)

    def technique_permitted(self, technique):
        if not technique:
            return True
        t = technique.lower()
        if any(t == p or p in t for p in self.prohibited):
            return False
        return any(t == p or p in t for p in self.permitted)

    # ---- required-elements + window ----
    def missing_required(self):
        r, missing = self.rec, []
        sig = r.get("authorized_signatory") or {}
        if not (r.get("signed") and sig.get("name") and sig.get("date")):
            missing.append("signed_authorization")
        insc = r.get("in_scope") or {}
        if not (insc.get("domains") or insc.get("cidrs") or insc.get("ips")):
            missing.append("in_scope_list")
        tw = r.get("test_window") or {}
        if not (parse_dt(tw.get("start")) and parse_dt(tw.get("end"))):
            missing.append("test_window")
        if not r.get("permitted_techniques"):
            missing.append("permitted_techniques")
        return missing

    def window_active(self, at=None):
        at = at or now_utc()
        tw = self.rec.get("test_window") or {}
        start, end = parse_dt(tw.get("start")), parse_dt(tw.get("end"))
        if not (start and end):
            return False
        return start <= at <= end

    def is_authorized(self, at=None):
        reasons = []
        missing = self.missing_required()
        if missing:
            reasons.append("missing required RoE elements: " + ", ".join(missing))
        if not self.window_active(at):
            reasons.append("outside the authorized test window")
        return (not reasons), reasons

    # ---- THE gate every active tool calls ----
    def authorize_action(self, target, technique=None, at=None):
        ok, reasons = self.is_authorized(at)
        if not ok:
            return {"allowed": False, "reasons": reasons}
        if not self.in_scope(target):
            return {"allowed": False, "reasons": [f"{target} is not in engagement {self.engagement_id} scope"]}
        if technique and not self.technique_permitted(technique):
            return {"allowed": False, "reasons": [f"technique '{technique}' is not a permitted test type"]}
        return {"allowed": True, "reasons": ["authorized: within window, in scope, permitted technique"]}


def blank_record(engagement_id, client):
    return {
        "engagement_id": engagement_id,
        "client": client,
        "testing_firm": "[YOUR FIRM]",
        "authorized_signatory": {"name": "[EMPOWERED CLIENT OWNER]", "title": "", "email": "", "date": ""},
        "signed": False,
        "in_scope": {"domains": ["[app.client.com]"], "cidrs": [], "ips": []},
        "out_of_scope": [],
        "test_window": {"start": "[YYYY-MM-DDT00:00:00Z]", "end": "[YYYY-MM-DDT23:59:59Z]", "hours": "anytime"},
        "permitted_techniques": ["web application testing", "API testing", "external network (non-DoS)"],
        "prohibited_techniques": ["denial-of-service", "social engineering", "physical intrusion",
                                  "modification/destruction of production data"],
        "sow_reference": "[SOW #, date]",
        "notes": "Fill every [PLACEHOLDER]; set signed=true only after the client's empowered signatory signs. "
                 "This machine record accompanies the full signed 'Authorization & Rules of Engagement' document."
    }


def _pretty_gate(label, res):
    mark = "ALLOWED" if res["allowed"] else "DENIED "
    print(f"  [{mark}] {label}")
    for r in res["reasons"]:
        print(f"           - {r}")


def self_test():
    rec = {
        "engagement_id": "ENG-TEST", "client": "Acme",
        "authorized_signatory": {"name": "Jane Owner", "title": "CISO", "date": "2026-07-01"},
        "signed": True,
        "in_scope": {"domains": ["acme.com"], "cidrs": ["203.0.113.0/24"], "ips": []},
        "out_of_scope": ["admin.acme.com"],
        "test_window": {"start": "2026-07-01T00:00:00Z", "end": "2026-12-31T23:59:59Z"},
        "permitted_techniques": ["web application testing", "API testing"],
        "prohibited_techniques": ["denial-of-service", "social engineering"],
    }
    e = Engagement(rec)
    at = parse_dt("2026-07-13T12:00:00Z")
    assert e.is_authorized(at)[0], "valid engagement should be authorized"
    assert e.authorize_action("https://app.acme.com", "web application testing", at)["allowed"]
    assert not e.authorize_action("evil.com", "web application testing", at)["allowed"], "out-of-scope must deny"
    assert not e.authorize_action("admin.acme.com", "web application testing", at)["allowed"], "exclusion must deny"
    assert not e.authorize_action("app.acme.com", "denial-of-service", at)["allowed"], "prohibited technique must deny"
    assert e.authorize_action("203.0.113.10", "API testing", at)["allowed"], "in-CIDR must allow"
    past = parse_dt("2027-01-05T00:00:00Z")
    assert not e.authorize_action("app.acme.com", "web application testing", past)["allowed"], "outside window must deny"
    # missing required elements
    bad = Engagement({"engagement_id": "X", "in_scope": {}, "signed": False})
    assert set(bad.missing_required()) >= {"signed_authorization", "in_scope_list", "test_window", "permitted_techniques"}
    print("SELF-TEST PASSED — authorization gate enforces required elements, test window, "
          "scope (domains/CIDR/exclusions), and permitted-vs-prohibited techniques.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Engagement & Scope Manager")
    ap.add_argument("--self-test", action="store_true")
    sub = ap.add_subparsers(dest="command")

    p_new = sub.add_parser("new")
    p_new.add_argument("--id", required=True)
    p_new.add_argument("--client", required=True)
    p_new.add_argument("--out")

    p_val = sub.add_parser("validate")
    p_val.add_argument("record")
    p_val.add_argument("--at")

    p_chk = sub.add_parser("check")
    p_chk.add_argument("record")
    p_chk.add_argument("--target", required=True)
    p_chk.add_argument("--technique")
    p_chk.add_argument("--at")

    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.command:
        ap.error("choose a command: new | validate | check (or --self-test)")

    if a.command == "new":
        rec = blank_record(a.id, a.client)
        out = json.dumps(rec, indent=2)
        if a.out:
            open(a.out, "w").write(out)
            print(f"wrote authorization record template to {a.out} — fill every [PLACEHOLDER] and set signed=true after signature")
        else:
            print(out)
        return 0

    e = Engagement(json.loads(open(a.record).read()))
    at = parse_dt(a.at) if getattr(a, "at", None) else None
    if a.command == "validate":
        ok, reasons = e.is_authorized(at)
        missing = e.missing_required()
        print(f"Engagement {e.engagement_id} — authorized for active testing: {'YES' if ok else 'NO'}")
        print(f"  required elements present: {'all' if not missing else 'MISSING: ' + ', '.join(missing)}")
        print(f"  test window active: {e.window_active(at)}")
        for r in reasons:
            print(f"  - {r}")
        return 0
    if a.command == "check":
        res = e.authorize_action(a.target, a.technique, at)
        _pretty_gate(f"{a.target}" + (f" · {a.technique}" if a.technique else ""), res)
        return 0


if __name__ == "__main__":
    sys.exit(main())
