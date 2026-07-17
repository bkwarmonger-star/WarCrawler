"""
Provenance stamp — every record's `provenance` field.

Answers "what produced this, when, from which public sources" so a finding, detection,
or verdict is auditable and never confused with something the model invented.
"""
import datetime
from typing import Any, Dict, List, Optional


def utcnow() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def stamp(engine: str, sources: Optional[List[str]] = None,
          extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    p = {"engine": engine, "generated_at": utcnow(), "sources": sources or []}
    if extra:
        p.update(extra)
    return p
