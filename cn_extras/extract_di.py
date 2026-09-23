"""Azure AI Document Intelligence fallback (docs/PLAN.md §5.6).

Bytes go straight from memory to the analyze call; nothing touches disk or
Blob storage. Every analysis is followed by ``delete_analyze_result`` in a
``finally`` block, so Microsoft drops the stored input and result immediately
instead of after its default 24 h. The whole path is off unless
CN_DI_ENABLED=true, and the server works without the SDK installed.
"""

import io
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

READ_MODEL = "prebuilt-read"
LAYOUT_MODEL = "prebuilt-layout"

DEFAULT_MAX_PAGES = 20
DEFAULT_TIMEOUT_S = 60
DEFAULT_MAX_BYTES = 25 * 1024 * 1024

_RANGE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+))?\s*$")


class DiUnavailable(Exception):
    """OCR can't run here (disabled, not configured, SDK missing, over limits)."""


class DiFailed(Exception):
    """The OCR call was attempted and failed (timeout, service error)."""


@dataclass
class DiResult:
    text: str
    model: str
    pages_analyzed: int


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"Invalid {name}={raw!r}; expected a positive integer."
        ) from exc
    if value <= 0:
        raise ValueError(f"Invalid {name}={raw!r}; expected a positive integer.")
    return value


def max_pages() -> int:
    return _int_env("CN_DI_MAX_PAGES", DEFAULT_MAX_PAGES)


def enabled() -> bool:
    return os.getenv("CN_DI_ENABLED", "false").strip().lower() == "true"


def status() -> Optional[str]:
    """None when OCR can run, otherwise a user-facing reason why not."""
    if not enabled():
        return "OCR (Document Intelligence) is not enabled on this server"
    if (
        not os.getenv("CN_DI_ENDPOINT", "").strip()
        or not os.getenv("CN_DI_KEY", "").strip()
    ):
        return "OCR is enabled but CN_DI_ENDPOINT / CN_DI_KEY are not configured"
    try:
        import azure.ai.documentintelligence  # noqa: F401
    except ImportError:
        return "OCR is enabled but the azure-ai-documentintelligence package is not installed"
    return None


def parse_pages(
    spec: Optional[str], page_count: Optional[int]
) -> Tuple[str, List[int]]:
    """Validate a page selection like "1-3,7" and apply the per-call cap.

    With no ``spec``, selects the first CN_DI_MAX_PAGES pages. Returns the
    normalised spec for the API and the page numbers it covers. Page numbers
    beyond ``page_count`` (when known) are dropped.
    """
    cap = max_pages()
    if not spec or not spec.strip():
        last = min(cap, page_count) if page_count else cap
        return f"1-{last}", list(range(1, last + 1))

    if len(spec) > 200:
        raise ValueError("page selection is too long; use ranges like '1-20'.")
    selected: List[int] = []
    seen = set()
    for chunk in spec.split(","):
        match = _RANGE.match(chunk)
        if not match:
            raise ValueError(
                f"invalid page range '{chunk.strip()}'; use e.g. '1-5' or '2,4,7-9'."
            )
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start:
            raise ValueError(f"invalid page range '{chunk.strip()}'.")
        if page_count:
            end = min(end, page_count)
        if end - start + 1 > cap * 4:
            raise ValueError(f"page range '{chunk.strip()}' is too large.")
        for page in range(start, end + 1):
            if page not in seen:
                seen.add(page)
                selected.append(page)
        if len(selected) > cap:
            raise ValueError(
                f"more than {cap} pages selected; at most {cap} per call. Ask for a smaller range."
            )
    if not selected:
        raise ValueError(
            f"no pages in '{spec}' (the document has {page_count} page(s))."
        )
    return _format_pages(selected), selected


def _format_pages(pages: List[int]) -> str:
    pages = sorted(pages)
    parts, start, prev = [], pages[0], pages[0]
    for p in pages[1:] + [None]:
        if p is not None and p == prev + 1:
            prev = p
            continue
        parts.append(f"{start}-{prev}" if prev != start else str(start))
        if p is not None:
            start = prev = p
    return ",".join(parts)


def _client():
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.core.credentials import AzureKeyCredential

    timeout = _int_env("CN_DI_TIMEOUT_S", DEFAULT_TIMEOUT_S)
    return DocumentIntelligenceClient(
        endpoint=os.environ["CN_DI_ENDPOINT"].strip(),
        credential=AzureKeyCredential(os.environ["CN_DI_KEY"].strip()),
        connection_timeout=min(timeout, 15),
        read_timeout=timeout,
        # Never resend after a read timeout: Azure may already have accepted
        # (and billed) the analysis, and we'd never learn the first result id
        # to delete it. Connect failures are safe to retry (nothing was sent);
        # one status retry covers 429 throttling. The SDK default would retry
        # up to 10 times, far past CN_DI_TIMEOUT_S.
        retry_total=2,
        retry_read=0,
        retry_status=1,
        retry_backoff_max=5,
    )


def analyze(data: bytes, model: str, pages: Optional[str]) -> DiResult:
    """Run one analysis and make sure its stored result gets deleted.

    Raises DiUnavailable before any network call when OCR can't be used, and
    DiFailed when the call fails or exceeds CN_DI_TIMEOUT_S (a wall-clock
    budget for the whole call). On timeout the operation keeps running at
    Azure; its result is deleted by a callback once it finishes.
    """
    reason = status()
    if reason:
        raise DiUnavailable(reason)
    try:
        timeout = _int_env("CN_DI_TIMEOUT_S", DEFAULT_TIMEOUT_S)
        max_bytes = _int_env("CN_DI_MAX_BYTES", DEFAULT_MAX_BYTES)
    except ValueError as exc:
        raise DiUnavailable(f"OCR is misconfigured: {exc}") from exc
    if len(data) > max_bytes:
        raise DiUnavailable("the file is larger than the OCR size limit")

    from azure.ai.documentintelligence.models import DocumentContentFormat
    from azure.core.exceptions import AzureError

    started = time.monotonic()
    client = None
    poller = None
    deferred = False
    try:
        client = _client()
        kwargs = {"pages": pages} if pages else {}
        if model == LAYOUT_MODEL:
            # Markdown keeps tables (as HTML tables) and checkboxes (☒/☐).
            kwargs["output_content_format"] = DocumentContentFormat.MARKDOWN
        poller = client.begin_analyze_document(
            model, io.BytesIO(data), content_type="application/octet-stream", **kwargs
        )
        remaining = max(1.0, timeout - (time.monotonic() - started))
        result = poller.result(timeout=remaining)
        if not poller.done():
            # Leave the client open: the SDK keeps polling in its own thread,
            # and the callback deletes the result (then closes) when it ends.
            deferred = _defer_cleanup(client, poller, model)
            raise DiFailed(f"OCR did not finish within {timeout} s")
        if result is None:
            raise DiFailed("OCR returned no result")
        return DiResult(
            text=(result.content or "").strip(),
            model=model,
            pages_analyzed=len(result.pages or []),
        )
    except (DiFailed, DiUnavailable):
        raise
    except AzureError as exc:
        # Service messages can echo request details; keep only the type.
        raise DiFailed(
            f"the OCR service returned an error ({type(exc).__name__})"
        ) from exc
    except Exception as exc:  # SDK/transport surprises: still a clean OCR failure
        raise DiFailed(f"the OCR call failed ({type(exc).__name__})") from exc
    finally:
        if not deferred and client is not None:
            _delete(client, model, _result_id(poller))
            client.close()
        logger.info(
            "[di] model=%s duration_ms=%d cleanup=%s",
            model,
            (time.monotonic() - started) * 1000,
            "deferred"
            if deferred
            else ("done" if _result_id(poller) else "no-result-id"),
        )


def _result_id(poller) -> Optional[str]:
    if poller is None:
        return None
    try:
        return poller.details.get("operation_id")
    except Exception:
        return None


def _defer_cleanup(client, poller, model: str) -> bool:
    """Delete + close once the still-running operation finishes.

    LROPoller runs its callbacks when the operation ends, whatever its outcome.
    Returns False when no callback could be registered (then the caller cleans
    up immediately; Azure's 24 h retention is the backstop).
    """
    result_id = _result_id(poller)
    done = threading.Event()

    def cleanup(_polling_method=None):
        if done.is_set():
            return
        done.set()
        _delete(client, model, result_id)
        client.close()

    try:
        poller.add_done_callback(cleanup)
    except Exception:
        return False
    return True


def _delete(client, model: str, result_id: Optional[str]) -> None:
    if not result_id:
        return
    try:
        client.delete_analyze_result(model, result_id)
    except Exception as exc:  # cleanup must never mask the real outcome
        # Azure still deletes it after 24 h; log so a broken cleanup is visible.
        logger.warning(
            "[di] delete_analyze_result failed for model=%s: %s",
            model,
            type(exc).__name__,
        )
