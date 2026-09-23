"""Render PDF pages to images: the last resort when OCR is unavailable.

Claude reads images natively, so a scanned page returned as an image is
still readable. pypdfium2 (Apache-2.0/BSD) renders in memory; the scale is
chosen per page so the long edge lands at MAX_LONG_EDGE_PX, never larger,
which also bounds memory for absurd page sizes.
"""

import io
import logging
import os
import threading
from typing import List

from cn_extras.extract_local import ExtractionFailed
from cn_extras.images import MAX_LONG_EDGE_PX, PreparedImage, prepare_image

logger = logging.getLogger(__name__)

DEFAULT_RENDER_MAX_PAGES = 5

# PDFium is not thread-safe and pypdfium2 does no locking; tools run extract()
# in worker threads concurrently, and a native crash would take the whole
# server down. Every pdfium call in this process goes through this lock.
PDFIUM_LOCK = threading.Lock()


def render_max_pages() -> int:
    raw = os.getenv("CN_RENDER_MAX_PAGES", "").strip()
    if not raw:
        return DEFAULT_RENDER_MAX_PAGES
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"Invalid CN_RENDER_MAX_PAGES={raw!r}; expected a positive integer."
        ) from exc
    if value <= 0:
        raise ValueError(
            f"Invalid CN_RENDER_MAX_PAGES={raw!r}; expected a positive integer."
        )
    return value


def render_pages(data: bytes, page_numbers: List[int]) -> List[PreparedImage]:
    """Render the given 1-based pages (at most CN_RENDER_MAX_PAGES) as images."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise ExtractionFailed("page rendering needs pypdfium2 (extra 'cn').") from exc

    wanted = page_numbers[: render_max_pages()]
    with PDFIUM_LOCK:
        return _render_locked(pdfium, data, wanted)


def _render_locked(pdfium, data: bytes, wanted: List[int]) -> List[PreparedImage]:
    images: List[PreparedImage] = []
    try:
        doc = pdfium.PdfDocument(io.BytesIO(data))
    except pdfium.PdfiumError as exc:
        raise ExtractionFailed("the PDF could not be opened for rendering.") from exc
    try:
        for number in wanted:
            if number < 1 or number > len(doc):
                continue
            page = doc[number - 1]
            try:
                width, height = page.get_size()  # PDF points, 1/72 inch
                scale = min(4.0, MAX_LONG_EDGE_PX / max(width, height, 1))
                pil = page.render(scale=scale).to_pil()
                buf = io.BytesIO()
                pil.convert("RGB").save(buf, format="JPEG", quality=80)
                images.append(prepare_image(buf.getvalue(), "image/jpeg"))
            finally:
                page.close()
    finally:
        doc.close()
    if not images:
        raise ExtractionFailed("none of the requested pages exist in this PDF.")
    return images
