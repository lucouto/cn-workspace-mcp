"""Single entry point for turning file bytes into something Claude can read.

``extract(data, mime_type, filename, mode)`` is used by every cn_extras read
tool. It never touches disk. Routing to the Azure Document Intelligence
fallback happens here (docs/PLAN.md §5.6), so tools don't choose engines:
local extraction first; OCR for scanned or garbled PDFs, multi-page TIFFs and
explicit ``mode="ocr"``; rendered page images as the last resort.
"""

import mimetypes
from dataclasses import dataclass, field
from typing import List, Literal, Optional

import io
import logging

from cn_extras import extract_local as local
from cn_extras import extract_di as di
from cn_extras import heuristics, quota
from cn_extras.images import (
    CONVERTIBLE_IMAGE_MIME_TYPES,
    NATIVE_IMAGE_MIME_TYPES,
    ImageNotUsableError,
    PreparedImage,
    prepare_image,
)

logger = logging.getLogger(__name__)

Mode = Literal["auto", "local", "ocr"]
Kind = Literal["text", "image", "unsupported"]

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
OFFICE_XML = {DOCX, XLSX, PPTX}

PLAIN_TEXT = {
    "text/plain",
    "text/csv",
    "text/tab-separated-values",
    "text/markdown",
    "text/x-markdown",
    "application/csv",
    "application/json",
    "text/calendar",
    "text/xml",
    "application/xml",
}
HTML = {"text/html", "application/xhtml+xml"}
EML = {"message/rfc822"}

LEGACY_OFFICE = {
    "application/msword": ".docx",
    "application/vnd.ms-excel": ".xlsx",
    "application/vnd.ms-powerpoint": ".pptx",
}

# Browsers and mail clients often send these instead of a real type.
_GENERIC = {
    "",
    "application/octet-stream",
    "binary/octet-stream",
    "application/x-download",
}

_EXTENSION_TYPES = {
    ".docx": DOCX,
    ".xlsx": XLSX,
    ".pptx": PPTX,
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".json": "application/json",
    ".eml": "message/rfc822",
    ".pdf": "application/pdf",
    ".heic": "image/heic",
    ".heif": "image/heif",
}

_UNSUPPORTED_REASONS = {
    "application/zip": "ZIP archives are not opened; ask for the individual files.",
    "application/x-zip-compressed": "ZIP archives are not opened; ask for the individual files.",
    "application/x-7z-compressed": "7z archives are not opened.",
    "application/x-rar-compressed": "RAR archives are not opened.",
    "application/vnd.rar": "RAR archives are not opened.",
    "image/svg+xml": "SVG images are not rendered.",
    "image/heic": "HEIC photos are not supported yet; ask for a JPEG.",
    "image/heif": "HEIF photos are not supported yet; ask for a JPEG.",
    "application/vnd.oasis.opendocument.text": "OpenDocument files are not supported; export to .docx or PDF.",
    "application/vnd.oasis.opendocument.spreadsheet": "OpenDocument files are not supported; export to .xlsx.",
}


@dataclass
class ExtractResult:
    kind: Kind
    mime_type: str
    engine: str
    text: Optional[str] = None
    images: List[PreparedImage] = field(default_factory=list)
    reason: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    page_count: Optional[int] = None

    @property
    def image_data(self) -> Optional[bytes]:
        return self.images[0].data if self.images else None

    @property
    def image_mime_type(self) -> Optional[str]:
        return self.images[0].mime_type if self.images else None


@dataclass
class OcrOptions:
    """Per-call context for the Document Intelligence fallback."""

    user: str = "unknown"
    pages: Optional[str] = None
    render_pages: bool = False


def _guess_from_filename(filename: Optional[str]) -> Optional[str]:
    if not filename or "." not in filename:
        return None
    ext = "." + filename.rsplit(".", 1)[1].lower()
    return _EXTENSION_TYPES.get(ext) or mimetypes.guess_type(filename)[0]


def normalize_mime_type(mime_type: Optional[str], filename: Optional[str]) -> str:
    """Strip parameters and replace generic types with a guess from the filename.

    A declared ``text/plain`` is only refined to another ``text/*`` type (e.g. a
    .csv), never turned into a binary type by a filename.
    """
    mime = (mime_type or "").split(";", 1)[0].strip().lower()
    if mime in _GENERIC:
        return _guess_from_filename(filename) or "application/octet-stream"
    if mime == "text/plain":
        guessed = _guess_from_filename(filename)
        if guessed and guessed.startswith("text/"):
            return guessed
    return mime


def is_extractable(mime_type: str, filename: Optional[str] = None) -> str:
    """Short capability label for listings: 'text', 'image', or 'no'."""
    mime = normalize_mime_type(mime_type, filename)
    if mime in NATIVE_IMAGE_MIME_TYPES or mime in CONVERTIBLE_IMAGE_MIME_TYPES:
        return "image"
    if (
        mime == "application/pdf"
        or mime in OFFICE_XML
        or mime in PLAIN_TEXT
        or mime in HTML
        or mime in EML
        or mime.startswith("text/")
    ):
        return "text"
    return "no"


def _unsupported(
    mime: str, reason: str, notes: Optional[List[str]] = None
) -> ExtractResult:
    return ExtractResult(
        kind="unsupported",
        mime_type=mime,
        engine="none",
        reason=reason,
        notes=notes or [],
    )


def _xlsx(
    data: bytes, mime: str, notes: List[str], sheet: Optional[str]
) -> ExtractResult:
    try:
        from cn_extras.office import xlsx_text
    except ImportError:  # openpyxl is in the optional "cn" extra
        if sheet:
            notes.append("sheet selection needs openpyxl; returned every sheet.")
        notes.append("read without sheet structure (openpyxl not installed).")
        text = local.office_text(data, mime)
        if not text:
            return _unsupported(mime, "the spreadsheet contains no text.", notes)
        return ExtractResult(
            kind="text", mime_type=mime, engine="local", text=text, notes=notes
        )
    book = xlsx_text(data, sheet)
    return ExtractResult(
        kind="text",
        mime_type=mime,
        engine="local",
        text=book.text,
        notes=notes + book.notes,
    )


def _pptx(data: bytes, mime: str, notes: List[str]) -> ExtractResult:
    from cn_extras.office import pptx_text

    return ExtractResult(
        kind="text", mime_type=mime, engine="local", text=pptx_text(data), notes=notes
    )


def extract(
    data: bytes,
    mime_type: Optional[str],
    filename: Optional[str] = None,
    mode: Mode = "auto",
    *,
    sheet: Optional[str] = None,
    ocr: Optional[OcrOptions] = None,
) -> ExtractResult:
    """Extract ``data``. ``sheet`` (number from 1, or name) limits XLSX output.

    ``mode``: 'auto' routes to OCR only when local extraction fails; 'local'
    never calls OCR; 'ocr' forces it for PDFs and images.
    """
    if mode not in ("auto", "local", "ocr"):
        raise ValueError("mode must be 'auto', 'local' or 'ocr'.")
    ocr = ocr or OcrOptions()
    mime = normalize_mime_type(mime_type, filename)
    notes: List[str] = []
    is_image = mime in NATIVE_IMAGE_MIME_TYPES or mime in CONVERTIBLE_IMAGE_MIME_TYPES
    if mode == "ocr" and mime != "application/pdf" and not is_image:
        notes.append("mode='ocr' applies to PDFs and images; used local extraction.")

    if not data:
        return _unsupported(mime, "the file is empty.", notes)

    try:
        if mime == "application/pdf":
            return _extract_pdf(data, mime, notes, mode, ocr)
        if mime == XLSX:
            return _xlsx(data, mime, notes, sheet)
        if mime == PPTX:
            return _pptx(data, mime, notes)
        if mime == DOCX:
            text = local.office_text(data, mime)
            if not text:
                return _unsupported(mime, "the document contains no text.", notes)
            return ExtractResult(
                kind="text", mime_type=mime, engine="local", text=text, notes=notes
            )
        if mime in EML:
            eml = local.extract_eml(data)
            return ExtractResult(
                kind="text", mime_type=mime, engine="local", text=eml.text, notes=notes
            )
        if mime in HTML:
            html, _ = local.decode_text(data)
            return ExtractResult(
                kind="text",
                mime_type=mime,
                engine="local",
                text=local.html_to_text(html),
                notes=notes,
            )
        if mime in PLAIN_TEXT or mime.startswith("text/"):
            if local.looks_binary(data):
                return _unsupported(
                    mime, "the file claims to be text but contains binary data.", notes
                )
            text, encoding = local.decode_text(data)
            if encoding not in ("utf-8-sig", "utf-16"):
                notes.append(f"decoded as {encoding} (not valid UTF-8).")
            return ExtractResult(
                kind="text", mime_type=mime, engine="local", text=text, notes=notes
            )
        if is_image:
            return _extract_image(data, mime, notes, mode, ocr)
    except local.ExtractionFailed as exc:
        return _unsupported(mime, str(exc), notes)
    except Exception as exc:  # RecursionError included (it is a RuntimeError)
        # Parsers (email, pypdf, zipfile, html) raise many exception types on
        # hostile input. Never let one escape as a generic tool crash with a
        # traceback in the logs; log the type only, never content.
        logger.warning("Extraction failed for %s: %s", mime, type(exc).__name__)
        return _unsupported(mime, "the file could not be parsed.", notes)

    if mime in LEGACY_OFFICE:
        return _unsupported(
            mime,
            f"legacy binary Office format; ask for a {LEGACY_OFFICE[mime]} or PDF version.",
            notes,
        )
    if mime in _UNSUPPORTED_REASONS:
        return _unsupported(mime, _UNSUPPORTED_REASONS[mime], notes)
    if mime.startswith(("audio/", "video/")):
        return _unsupported(mime, "audio and video files are not transcribed.", notes)
    return _unsupported(mime, f"no extractor for {mime}.", notes)


# --- OCR routing -------------------------------------------------------------


class _OcrNotUsed(Exception):
    """OCR was wanted but couldn't produce text; the message says why."""


def _run_ocr(
    data: bytes, model: str, pages: Optional[str], n_pages: int, user: str
) -> str:
    """Reserve quota, analyze, settle. Raises _OcrNotUsed with a reason."""
    reason = di.status()
    if reason:
        raise _OcrNotUsed(reason)
    try:
        reservation = quota.reserve(user, n_pages)
    except quota.QuotaExceeded as exc:
        raise _OcrNotUsed(str(exc)) from exc
    billed = 0
    try:
        result = di.analyze(data, model, pages)
        billed = result.pages_analyzed or n_pages
    except di.DiUnavailable as exc:
        raise _OcrNotUsed(str(exc)) from exc
    except di.DiFailed as exc:
        billed = n_pages  # the service may have billed; stay conservative
        raise _OcrNotUsed(str(exc)) from exc
    finally:
        quota.settle(reservation, n_pages, billed)
    if not result.text:
        raise _OcrNotUsed("OCR found no text on the selected page(s)")
    return result.text


def _engine(model: str) -> str:
    return "di-layout" if model == di.LAYOUT_MODEL else "di-read"


def _page_images(
    data: bytes, mime: str, selected: List[int], notes: List[str], page_count: int
) -> ExtractResult:
    from cn_extras.pages import render_max_pages, render_pages

    images = render_pages(data, selected)
    shown = selected[: len(images)]
    notes.append(
        f"page(s) {di._format_pages(shown)} of {page_count} rendered as images "
        f"(at most {render_max_pages()} per call; use pages= for others)."
    )
    return ExtractResult(
        kind="image",
        mime_type=mime,
        engine="page-images",
        images=images,
        notes=notes,
        page_count=page_count,
    )


def _extract_pdf(
    data: bytes, mime: str, notes: List[str], mode: str, ocr: OcrOptions
) -> ExtractResult:
    # Encrypted or damaged PDFs raise here and are never sent to OCR.
    pdf = local.extract_pdf(data)
    scanned = heuristics.is_scanned(pdf.pages)

    why, model = None, di.READ_MODEL
    if mode == "ocr":
        why, model = "requested", di.LAYOUT_MODEL
    elif mode == "auto" and scanned:
        why = "no usable text layer"
    elif mode == "auto" and heuristics.looks_garbled("\n".join(pdf.pages)):
        why, model = "table layout came out garbled", di.LAYOUT_MODEL

    needs_pages = bool(why) or (ocr.render_pages and (scanned or not pdf.total_chars))
    spec, selected = "", []
    if needs_pages:
        try:
            spec, selected = di.parse_pages(ocr.pages, pdf.page_count)
        except ValueError as exc:
            return _unsupported(mime, str(exc), notes)
        if not ocr.pages and scanned:
            # In a mixed PDF, spend the page budget on pages that lack text,
            # not on the first N (which may be the ones that already have it).
            thin = [
                i
                for i, text in enumerate(pdf.pages, 1)
                if len(text) < heuristics.scanned_chars_per_page()
            ][: di.max_pages()]
            if thin:
                selected = thin
                spec = di._format_pages(thin)
    elif ocr.pages:
        notes.append(
            "pages applies to OCR and page rendering; this PDF has a text layer, "
            "so the full text is returned (paginate with offset)."
        )

    if why:
        try:
            text = _run_ocr(data, model, spec, len(selected), ocr.user)
            notes.append(
                f"OCR ({model}, reason: {why}) on page(s) {spec} of {pdf.page_count}."
            )
            ocr_pages = set(selected)
            kept = [
                (i, page)
                for i, page in enumerate(pdf.pages, 1)
                if page and i not in ocr_pages
            ]
            if kept:
                # Never drop a real text layer just because other pages needed OCR.
                text += (
                    "\n\n=== Pages not OCR'd (from the PDF's own text layer) ===\n\n"
                )
                text += "\n\n".join(f"--- page {i} ---\n{page}" for i, page in kept)
                notes.append(
                    f"{len(kept)} other page(s) come from the PDF's text layer."
                )
            missing = pdf.page_count - len(selected) - len(kept)
            if missing > 0:
                notes.append(
                    f"{missing} page(s) without text were not OCR'd; ask for them with pages=."
                )
            return ExtractResult(
                kind="text",
                mime_type=mime,
                engine=_engine(model),
                text=text,
                notes=notes,
                page_count=pdf.page_count,
            )
        except _OcrNotUsed as exc:
            # A disabled OCR is not news when we only suspected a garbled table.
            if not (why.startswith("table") and di.status()):
                notes.append(f"OCR not used: {exc}.")

    if pdf.total_chars and not scanned:
        return _local_pdf_result(pdf, mime, notes)
    if ocr.render_pages:
        return _page_images(data, mime, selected, notes, pdf.page_count)
    if pdf.total_chars:
        notes.append(
            "the text layer is very thin; pass render_pages=true to see the pages."
        )
        return _local_pdf_result(pdf, mime, notes)
    return ExtractResult(
        kind="unsupported",
        mime_type=mime,
        engine="none",
        reason=(
            f"no text layer found in {pdf.page_count} page(s); the PDF is probably "
            "scanned. Pass render_pages=true to get the pages as images."
        ),
        notes=notes,
        page_count=pdf.page_count,
    )


def _local_pdf_result(pdf, mime: str, notes: List[str]) -> ExtractResult:
    empty = sum(1 for p in pdf.pages if not p)
    if empty:
        notes.append(
            f"{empty} of {pdf.page_count} page(s) have no text layer (scanned or image-only)."
        )
    return ExtractResult(
        kind="text",
        mime_type=mime,
        engine="local",
        text=pdf.joined(),
        notes=notes,
        page_count=pdf.page_count,
    )


# Formats Document Intelligence accepts (GIF and WebP are not among them).
OCR_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/bmp",
    "image/x-ms-bmp",
    "image/tiff",
}


def _frame_count(data: bytes) -> Optional[int]:
    """Number of pages in a TIFF, or None when Pillow can't tell."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            return int(getattr(img, "n_frames", 1) or 1)
    except Exception:
        return None


def _extract_image(
    data: bytes, mime: str, notes: List[str], mode: str, ocr: OcrOptions
) -> ExtractResult:
    is_tiff = mime == "image/tiff"
    frames = _frame_count(data) if is_tiff else 1
    try:
        img = prepare_image(data, mime)
        image_error = None
    except ImageNotUsableError as exc:
        img, image_error = None, exc

    why, model = None, di.READ_MODEL
    if mode == "ocr":
        why, model = "requested", di.LAYOUT_MODEL
    elif mode == "auto" and (frames or 1) > 1:
        why = f"multi-page TIFF ({frames} pages)"
    elif mode == "auto" and image_error is not None:
        why = "image too large to return"

    if why and mime not in OCR_IMAGE_TYPES:
        notes.append(f"OCR does not support {mime}; not attempted.")
        why = None

    if why:
        try:
            if is_tiff:
                # Always bound a TIFF with an explicit page range: when the
                # frame count is unknown, the default range (first
                # CN_DI_MAX_PAGES) is what gets reserved and analyzed.
                spec, selected = di.parse_pages(ocr.pages, frames)
                text = _run_ocr(data, model, spec, len(selected), ocr.user)
            else:
                text = _run_ocr(data, model, None, 1, ocr.user)
            notes.append(f"OCR ({model}, reason: {why}).")
            return ExtractResult(
                kind="text",
                mime_type=mime,
                engine=_engine(model),
                text=text,
                notes=notes,
            )
        except (_OcrNotUsed, ValueError) as exc:
            notes.append(f"OCR not used: {exc}.")

    if img is None:
        return _unsupported(mime, f"image could not be returned: {image_error}", notes)
    if img.note:
        notes.append(img.note)
    if frames and frames > 1:
        notes.append(f"this TIFF has {frames} pages; only the first is shown.")
    return ExtractResult(
        kind="image", mime_type=mime, engine="image", images=[img], notes=notes
    )
