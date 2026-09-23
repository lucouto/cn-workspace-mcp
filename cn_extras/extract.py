"""Single entry point for turning file bytes into something Claude can read.

``extract(data, mime_type, filename, mode)`` is used by every cn_extras read
tool. It never touches disk. Phase 2b plugs the Azure Document Intelligence
fallback in here (see docs/PLAN.md §5.6); until then ``mode="ocr"`` falls back
to local extraction with a note.
"""

import mimetypes
from dataclasses import dataclass, field
from typing import List, Literal, Optional

import logging

from cn_extras import extract_local as local
from cn_extras.images import (
    CONVERTIBLE_IMAGE_MIME_TYPES,
    NATIVE_IMAGE_MIME_TYPES,
    ImageNotUsableError,
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
    image_data: Optional[bytes] = None
    image_mime_type: Optional[str] = None
    reason: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    page_count: Optional[int] = None


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


def extract(
    data: bytes,
    mime_type: Optional[str],
    filename: Optional[str] = None,
    mode: Mode = "auto",
) -> ExtractResult:
    mime = normalize_mime_type(mime_type, filename)
    notes: List[str] = []
    if mode == "ocr":
        notes.append(
            "OCR (Document Intelligence) is not available yet; used local extraction."
        )
    elif mode not in ("auto", "local"):
        raise ValueError("mode must be 'auto', 'local' or 'ocr'.")

    if not data:
        return _unsupported(mime, "the file is empty.", notes)

    try:
        if mime == "application/pdf":
            return _extract_pdf(data, mime, notes)
        if mime in OFFICE_XML:
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
        if mime in NATIVE_IMAGE_MIME_TYPES or mime in CONVERTIBLE_IMAGE_MIME_TYPES:
            try:
                img = prepare_image(data, mime)
            except ImageNotUsableError as exc:
                return _unsupported(mime, f"image could not be returned: {exc}", notes)
            if img.note:
                notes.append(img.note)
            return ExtractResult(
                kind="image",
                mime_type=mime,
                engine="image",
                image_data=img.data,
                image_mime_type=img.mime_type,
                notes=notes,
            )
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


def _extract_pdf(data: bytes, mime: str, notes: List[str]) -> ExtractResult:
    pdf = local.extract_pdf(data)
    if pdf.total_chars == 0:
        return ExtractResult(
            kind="unsupported",
            mime_type=mime,
            engine="none",
            reason=(
                f"no text layer found in {pdf.page_count} page(s); the PDF is "
                "probably scanned. OCR support is planned (Document Intelligence)."
            ),
            notes=notes,
            page_count=pdf.page_count,
        )
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
