"""
Self-test convention — the quality gate every skill in the suite MUST carry.

Each skill module exposes `_self_test() -> int` (0 = pass) and a `--self-test` CLI flag.
Skills register here at import so the Arsenal Regression Runner can execute the whole
suite in one command. A skill is not "vetted" until its self-test is green.

Definition of Done for any wave-1..3 agent: its package's registered self-tests pass
AND `pytest` is green.
"""
import sys
import traceback
from typing import Callable, Dict, List, Tuple

_REGISTRY: Dict[str, Callable[[], int]] = {}


def register(name: str, fn: Callable[[], int]) -> None:
    _REGISTRY[name] = fn


def registered() -> List[str]:
    return sorted(_REGISTRY)


def run_all(verbose: bool = True) -> Tuple[int, int]:
    """Run every registered self-test. Returns (passed, total)."""
    passed = 0
    for name in registered():
        try:
            rc = _REGISTRY[name]()
            ok = rc == 0
        except Exception:
            ok = False
            if verbose:
                traceback.print_exc()
        passed += ok
        if verbose:
            print(("[PASS] " if ok else "[FAIL] ") + name)
    total = len(_REGISTRY)
    if verbose:
        print("=== %d/%d self-tests passed ===" % (passed, total))
    return passed, total


def cli(fn: Callable[[], int]) -> None:
    """Tiny helper: `if '--self-test' in argv: sys.exit(fn())`."""
    if "--self-test" in sys.argv:
        sys.exit(fn())
