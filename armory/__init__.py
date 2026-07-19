"""
armory/ — the toolsmith. Builds, imports, validates, catalogs security tooling for
Aegis/Bastion, and keeps the arsenal measurable. Workshop-autonomous; arming a live
arsenal and any live-target run stay human-gated.

Two vetted tools ship here (extracted verbatim from the Armory agent export, self-tests
green): `tools/awa.py` (Active Web Assessment — scope-guarded HTTP probe + Nuclei runner)
and `tools/engagement.py` (Engagement & Scope Manager — the canonical authorization gate).

`engagement.Engagement` is the canonical RoE gate the whole suite defers to (orchestrator
wraps it). `catalog.json` registers every tool across all three agents + its methodology
anchor + pipeline stage, so coverage/gaps are computable.
"""
from .tools import engagement, awa  # noqa: F401

__all__ = ["engagement", "awa"]
