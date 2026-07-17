"""
MITRE ATT&CK loader — the shared coordinate system for the whole suite.

Aegis maps attacks to technique ids; Bastion maps detections + mitigations to the SAME
ids; the coverage matrix + emulation planner overlay them. One loader, one cached copy.

Enterprise STIX, cached in the system temp dir. Deterministic index; the model narrates
after. `ATTACK_VERSION` is the single version string every record stamps into its
`attack.version` field (reconcile D3FEND's embedded lag against this).
"""
import json
import os
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple

from . import http

ATTACK_VERSION = "19.1"
STIX_URL = ("https://raw.githubusercontent.com/mitre-attack/attack-stix-data/"
            "master/enterprise-attack/enterprise-attack.json")
STIX_CACHE = os.path.join(tempfile.gettempdir(), "enterprise-attack.json")
CACHE_TTL = 7 * 24 * 3600

_INDEX: Optional[Dict[str, Any]] = None


def _ext_id(o: Dict[str, Any]) -> Optional[str]:
    for r in o.get("external_references", []):
        if r.get("source_name") == "mitre-attack":
            return r.get("external_id")
    return None


def load(force: bool = False) -> Dict[str, Any]:
    """Download (cached) + index the STIX bundle. Returns the index; safe to call often."""
    global _INDEX
    if _INDEX is not None and not force:
        return _INDEX
    http.download(STIX_URL, STIX_CACHE, cache_ttl=CACHE_TTL)
    with open(STIX_CACHE, encoding="utf-8") as f:
        objs = json.load(f)["objects"]

    tech: Dict[str, Dict[str, Any]] = {}   # stix id -> {id, name, tactics}
    for o in objs:
        if o.get("type") == "attack-pattern" and not o.get("revoked") and not o.get("x_mitre_deprecated"):
            tid = _ext_id(o)
            if tid:
                tech[o["id"]] = {"id": tid, "name": o.get("name", ""),
                                 "tactics": [p["phase_name"] for p in o.get("kill_chain_phases", [])]}
    groups: Dict[str, Dict[str, Any]] = {}
    for o in objs:
        if o.get("type") == "intrusion-set" and not o.get("revoked"):
            groups[o["id"]] = {"id": _ext_id(o), "name": o.get("name", ""), "aliases": o.get("aliases", [])}
    uses: Dict[str, Set[str]] = {}
    for o in objs:
        if o.get("type") == "relationship" and o.get("relationship_type") == "uses":
            s, t = o.get("source_ref", ""), o.get("target_ref", "")
            if s.startswith("intrusion-set--") and t.startswith("attack-pattern--"):
                uses.setdefault(s, set()).add(t)

    _INDEX = {"tech": tech, "groups": groups, "uses": uses,
              "by_tid": {v["id"]: v for v in tech.values()}}
    return _INDEX


def technique(tid: str) -> Optional[Dict[str, Any]]:
    return load()["by_tid"].get(tid)


def resolve_group(query: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Resolve an ATT&CK group by id / name / alias (e.g. 'G0016', 'APT29', 'Cozy Bear')."""
    idx = load()
    q = query.strip().lower()
    for sid, g in idx["groups"].items():
        if (g["id"] or "").lower() == q or g["name"].lower() == q or q in [a.lower() for a in g["aliases"]]:
            return sid, g
    for sid, g in idx["groups"].items():
        if q in g["name"].lower() or any(q in a.lower() for a in g["aliases"]):
            return sid, g
    return None, None


def group_techniques(query: str) -> List[str]:
    """Sorted ATT&CK technique ids used by a group."""
    idx = load()
    sid, _ = resolve_group(query)
    if not sid:
        return []
    tech = idx["tech"]
    ids = {tech[t]["id"] for t in idx["uses"].get(sid, set()) if t in tech}
    return sorted(ids, key=lambda x: (int(x[1:].split(".")[0]), x))


def parent(tid: str) -> str:
    """Parent technique of a sub-technique ('T1059.001' -> 'T1059')."""
    return tid.split(".")[0]
