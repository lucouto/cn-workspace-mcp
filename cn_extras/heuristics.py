"""Decide when local PDF extraction isn't good enough (docs/PLAN.md §5.6).

Two signals route a PDF to Document Intelligence in ``mode="auto"``:
- scanned: too few characters per page for a text layer to be real;
- garbled tables: text came out, but as columns of short fragments split by
  long whitespace runs, which is what pypdf makes of tabular layouts.
Thresholds are env-tunable so they can be calibrated on real documents.
"""

import os
import re
from typing import List

_SPACE_RUN = re.compile(r" {3,}")
# Below this many non-empty lines there's too little text to judge layout.
MIN_LINES_TO_JUDGE = 20


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid {name}={raw!r}; expected a number.") from exc
    if value < 0:
        raise ValueError(f"Invalid {name}={raw!r}; expected a non-negative number.")
    return value


def scanned_chars_per_page() -> float:
    return _float_env("CN_DI_SCANNED_CHARS_PER_PAGE", 50)


def is_scanned(pages: List[str]) -> bool:
    """True when the average text per page is below the scanned threshold."""
    if not pages:
        return False
    return sum(len(p) for p in pages) / len(pages) < scanned_chars_per_page()


def looks_garbled(text: str) -> bool:
    """True when most lines are short fragments separated by wide gaps.

    Metric (PLAN §5.6): over non-empty lines, the share shorter than 15
    characters exceeds CN_DI_GARBLED_SHORT_LINE_RATIO (default 0.4) AND the
    average number of 3+-space runs per line exceeds CN_DI_GARBLED_SPACE_RUNS
    (default 3). Both must hold: short lines alone are just a list, and wide
    gaps alone are just justified text.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < MIN_LINES_TO_JUDGE:
        return False
    short_ratio = sum(1 for line in lines if len(line.strip()) < 15) / len(lines)
    runs_per_line = sum(len(_SPACE_RUN.findall(line)) for line in lines) / len(lines)
    return short_ratio > _float_env(
        "CN_DI_GARBLED_SHORT_LINE_RATIO", 0.4
    ) and runs_per_line > _float_env("CN_DI_GARBLED_SPACE_RUNS", 3)
