"""Per-user daily page quota for Document Intelligence (cost control).

In-memory and per process, reset at UTC midnight: enough for one container.
If the server ever runs several replicas, move this to Valkey (PLAN §5.6).
Pages are reserved before the call (so concurrent calls can't overshoot)
and the reservation is corrected to the pages actually billed afterwards.
"""

import datetime as dt
import logging
import os
import threading
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

DEFAULT_DAILY_PAGES_PER_USER = 200

_lock = threading.Lock()
_used: Dict[Tuple[str, str], int] = {}


class QuotaExceeded(Exception):
    """The user has no Document Intelligence pages left today."""


def daily_limit() -> int:
    raw = os.getenv("CN_DI_DAILY_PAGES_PER_USER", "").strip()
    if not raw:
        return DEFAULT_DAILY_PAGES_PER_USER
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"Invalid CN_DI_DAILY_PAGES_PER_USER={raw!r}; expected a non-negative integer."
        ) from exc
    if value < 0:
        raise ValueError(
            f"Invalid CN_DI_DAILY_PAGES_PER_USER={raw!r}; expected a non-negative integer."
        )
    return value


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _prune(today: str) -> None:
    for key in [k for k in _used if k[1] != today]:
        del _used[key]


def reserve(user: str, pages: int) -> Tuple[str, str]:
    """Reserve ``pages`` for ``user`` today, or raise QuotaExceeded.

    Returns the reservation key; pass it to ``settle`` so a call that crosses
    midnight UTC corrects the day it was reserved on.
    """
    limit = daily_limit()
    today = _today()
    with _lock:
        _prune(today)
        used = _used.get((user, today), 0)
        if used + pages > limit:
            raise QuotaExceeded(
                f"daily OCR quota reached ({used} of {limit} pages used today, "
                f"this file needs {pages}). It resets at midnight UTC; you can "
                "also read fewer pages with the pages parameter."
            )
        _used[(user, today)] = used + pages
    return (user, today)


def settle(key: Tuple[str, str], reserved: int, actual: int) -> None:
    """Replace a reservation with the pages actually billed, and log totals."""
    user, day = key
    today = _today()
    with _lock:
        if day == today:
            if key in _used:
                _used[key] = max(0, _used[key] - reserved + actual)
        else:
            # Reserved before midnight UTC: release it there, bill today.
            if key in _used:
                _used[key] = max(0, _used[key] - reserved)
            _used[(user, today)] = _used.get((user, today), 0) + actual
        user_total = _used.get((user, today), 0)
        all_total = sum(v for (_, d), v in _used.items() if d == today)
    logger.info(
        "[di-quota] user=%s pages=%d user_today=%d all_users_today=%d",
        user,
        actual,
        user_total,
        all_total,
    )


def reset_for_tests() -> None:
    with _lock:
        _used.clear()
