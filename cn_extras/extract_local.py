"""Local (in-process, no network) text extraction.

Office files go through upstream's ``core.utils.extract_office_xml_text``.
PDFs use pypdf directly rather than upstream's ``extract_pdf_text`` because we
need the page count, per-page text (for page markers and the scanned-PDF
heuristic of Phase 2b) and an explicit encrypted-PDF check.
"""

import email
import email.policy
import io
import logging
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class ExtractionFailed(ValueError):
    """The file could not be read; the message is safe to show the user."""


# --- PDF ---------------------------------------------------------------------


@dataclass
class PdfText:
    pages: List[str]
    page_count: int

    @property
    def total_chars(self) -> int:
        return sum(len(p) for p in self.pages)

    @property
    def chars_per_page(self) -> float:
        return self.total_chars / self.page_count if self.page_count else 0.0

    def joined(self) -> str:
        return "\n\n".join(
            f"--- page {i} ---\n{text}" for i, text in enumerate(self.pages, 1)
        )


def extract_pdf(data: bytes) -> PdfText:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # Many PDFs only carry an owner password ("no printing") and open
            # with an empty user password; real user passwords we can't bypass.
            try:
                if not reader.decrypt(""):
                    raise ExtractionFailed(
                        "the PDF is password-protected; ask the sender for an "
                        "unlocked copy."
                    )
            except ExtractionFailed:
                raise
            except Exception as exc:
                raise ExtractionFailed(
                    "the PDF is encrypted with an unsupported method."
                ) from exc
        pages = []
        for page in reader.pages:
            try:
                pages.append((page.extract_text() or "").strip())
            except Exception:
                pages.append("")
        return PdfText(pages=pages, page_count=len(reader.pages))
    except ExtractionFailed:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError, OSError) as exc:
        raise ExtractionFailed(
            "the PDF appears damaged and could not be parsed."
        ) from exc


# --- Text decoding -------------------------------------------------------------


_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def looks_binary(data: bytes) -> bool:
    # UTF-16 text (Excel's "Unicode Text" export) is full of NUL bytes.
    if data.startswith(_UTF16_BOMS):
        return False
    return b"\x00" in data[:8192]


def decode_text(data: bytes) -> Tuple[str, str]:
    """Decode bytes as UTF-8 (BOM-aware), falling back to Windows-1252, then Latin-1.

    French/Portuguese CSV exports from Excel are typically Windows-1252; its
    "Unicode Text" export is UTF-16 with a BOM.
    """
    if data.startswith(_UTF16_BOMS):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


# --- HTML --------------------------------------------------------------------

_BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "header",
    "footer",
    "li",
    "ul",
    "ol",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "table",
    "tr",
    "blockquote",
    "pre",
    "hr",
    "dl",
    "dt",
    "dd",
    "form",
}
# Only elements whose text is never page content. Not "head" or "svg": HTML5
# lets </head> be omitted and an unclosed <svg> would swallow the rest.
_SKIP_TAGS = {"script", "style", "title", "noscript", "template"}


class _HtmlToText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            # Whatever was left unclosed before <body> cannot hide the body.
            self._skip_depth = 0
        elif tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "br":
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append("\t")
        elif tag in _BLOCK_TAGS:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.out.append(data)


def html_to_text(html: str) -> str:
    parser = _HtmlToText()
    parser.feed(html)
    parser.close()
    lines = []
    for raw in "".join(parser.out).splitlines():
        # Collapse whitespace inside each table cell but keep the tab separators.
        cells = [" ".join(cell.split()) for cell in raw.split("\t")]
        lines.append("\t".join(cells).strip())
    # Collapse runs of blank lines to one.
    text, blank = [], False
    for line in lines:
        if not line:
            if not blank and text:
                text.append("")
            blank = True
        else:
            text.append(line)
            blank = False
    return "\n".join(text).strip()


# --- Attached e-mail (.eml / message/rfc822) ------------------------------------------


@dataclass
class EmailText:
    text: str
    attachment_names: List[str] = field(default_factory=list)


def _header(msg, raw_msg, name: str) -> str:
    # The modern policy parses address headers and can raise on hostile input
    # (IndexError, deep recursion); fall back to the unparsed value.
    try:
        return str(msg[name] or "")
    except Exception:
        return str(raw_msg.get(name) or "")


def extract_eml(data: bytes) -> EmailText:
    msg = email.message_from_bytes(data, policy=email.policy.default)
    raw_msg = email.message_from_bytes(data)  # compat32: headers left unparsed
    header_lines = [
        f"{name}: {value}"
        for name in ("From", "To", "Cc", "Date", "Subject")
        if (value := _header(msg, raw_msg, name))
    ]
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        try:
            content = body_part.get_content()
        except (LookupError, UnicodeDecodeError, ValueError):
            content = decode_text(body_part.get_payload(decode=True) or b"")[0]
        body = (
            html_to_text(content)
            if body_part.get_content_subtype() == "html"
            else content
        )
    names = [
        part.get_filename() for part in msg.iter_attachments() if part.get_filename()
    ]
    text = "\n".join(header_lines) + "\n\n" + (body or "").strip()
    if names:
        text += "\n\n[Attachments inside this e-mail: " + ", ".join(names) + "]"
    return EmailText(text=text.strip(), attachment_names=names)


def office_text(data: bytes, mime_type: str) -> Optional[str]:
    from core.utils import (
        OfficeXmlExtractionError,
        OfficeXmlTooLargeError,
        extract_office_xml_text,
    )

    try:
        return extract_office_xml_text(data, mime_type)
    except OfficeXmlTooLargeError as exc:
        raise ExtractionFailed(str(exc)) from exc
    except OfficeXmlExtractionError as exc:
        raise ExtractionFailed(
            f"the Office file appears damaged or is not a valid Office document ({exc})."
        ) from exc
