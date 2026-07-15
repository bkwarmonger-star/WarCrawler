"""Summarize what changed between two versions of a page's text."""
from __future__ import annotations

import difflib
from typing import Dict


def summarize_diff(old: str, new: str, max_sample_lines: int = 8) -> Dict[str, object]:
    """Return {added, removed, added_sample} for a line-level diff old -> new."""
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    added = 0
    removed = 0
    sample = []
    for line in difflib.unified_diff(old_lines, new_lines, lineterm="", n=0):
        if line[:3] in ("+++", "---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
            text = line[1:].strip()
            if text and len(sample) < max_sample_lines:
                sample.append(text)
        elif line.startswith("-"):
            removed += 1
    return {"added": added, "removed": removed,
            "added_sample": " | ".join(sample)[:500]}
