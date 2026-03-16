"""Logging configuration for Fish Finder.

Logs are written to ./logs/ with one file per run. Old files are pruned
automatically so the directory stays manageable.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

_LOG_DIR = Path("logs")
_current_log_file: Path | None = None


def setup(*, verbose: bool = False) -> Path:
    """Configure root fish_finder logger. Returns the log file path."""
    global _current_log_file

    _LOG_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = _LOG_DIR / f"fish_finder_{timestamp}.log"
    _current_log_file = log_file

    root = logging.getLogger("fish_finder")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(fh)

    if verbose:
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        ch.setFormatter(logging.Formatter("  %(levelname)-8s  %(message)s"))
        root.addHandler(ch)

    _prune_logs(keep=20)
    return log_file


def get_log_file() -> Path | None:
    return _current_log_file


def _prune_logs(keep: int = 20) -> None:
    logs = sorted(_LOG_DIR.glob("fish_finder_*.log"))
    for old in logs[:-keep]:
        old.unlink(missing_ok=True)
