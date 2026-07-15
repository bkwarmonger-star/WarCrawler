"""Logging setup. Uses rich if available, falls back to plain stdlib logging."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

try:  # pragma: no cover - optional dependency
    from rich.logging import RichHandler
    _HAS_RICH = True
except Exception:  # pragma: no cover
    _HAS_RICH = False


def setup_logging(level: str = "INFO", log_file: Optional[Path] = None) -> logging.Logger:
    logger = logging.getLogger("warcrawler")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    if _HAS_RICH:
        console_handler = RichHandler(rich_tracebacks=True, show_path=False,
                                      markup=False)
        console_handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
    else:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
        )
    logger.addHandler(console_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
        )
        logger.addHandler(file_handler)

    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("warcrawler")
