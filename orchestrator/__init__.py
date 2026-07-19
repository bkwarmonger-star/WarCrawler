"""orchestrator/ — the suite runtime (no claude.ai wrapper): gates, pipelines, schedule.

Only `gates` is provided at wave-0 (the keystone the active-testing layer needs early).
Agent I fills in pipeline.py / schedule.py / report_assembly.py in wave 3.
"""
from . import gates

__all__ = ["gates"]
