"""Test fixtures for cn_extras, generated in code (no binary blobs in the repo)."""

import base64
import io
import zipfile
from email.message import EmailMessage

from tests.helpers import _make_minimal_pdf

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
_P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
_S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, xml in members.items():
            zf.writestr(name, xml)
    return buf.getvalue()


def docx_with_table() -> bytes:
    def p(text):
        return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"

    table = (
        "<w:tbl>"
        f"<w:tr><w:tc>{p('Nom')}</w:tc><w:tc>{p('Pays')}</w:tc></w:tr>"
        f"<w:tr><w:tc>{p('João')}</w:tc><w:tc>{p('Brésil')}</w:tc></w:tr>"
        "</w:tbl>"
    )
    body = p("Inscriptions JMJ 2027") + table
    return _zip(
        {
            "word/document.xml": f'<?xml version="1.0"?><w:document {_W}><w:body>{body}</w:body></w:document>'
        }
    )


def xlsx_two_sheets() -> bytes:
    strings = ["Sheet one value", "Second sheet value"]
    sst = "".join(f"<si><t>{s}</t></si>" for s in strings)

    def sheet(index):
        return (
            f'<?xml version="1.0"?><worksheet {_S}><sheetData>'
            f'<row r="1"><c r="A1" t="s"><v>{index}</v></c></row>'
            "</sheetData></worksheet>"
        )

    return _zip(
        {
            "xl/workbook.xml": f'<?xml version="1.0"?><workbook {_S}><sheets>'
            '<sheet name="One" sheetId="1"/><sheet name="Two" sheetId="2"/>'
            "</sheets></workbook>",
            "xl/sharedStrings.xml": f'<?xml version="1.0"?><sst {_S}>{sst}</sst>',
            "xl/worksheets/sheet1.xml": sheet(0),
            "xl/worksheets/sheet2.xml": sheet(1),
        }
    )


def pptx_one_slide(text: str = "Slide title") -> bytes:
    return _zip(
        {
            "ppt/presentation.xml": f'<?xml version="1.0"?><p:presentation {_P}/>',
            "ppt/slides/slide1.xml": f'<?xml version="1.0"?><p:sld {_P} {_A}><p:cSld><p:spTree>'
            f"<p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>"
            "</p:spTree></p:cSld></p:sld>",
        }
    )


def text_pdf(text: str = "Hello World") -> bytes:
    return _make_minimal_pdf(text)


def blank_pdf(pages: int = 2) -> bytes:
    """A PDF with pages but no text layer, like a scan."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def encrypted_pdf(
    user_password: str = "secret", owner_password: str = "owner"
) -> bytes:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(text_pdf("Confidential"))))
    writer.encrypt(user_password=user_password, owner_password=owner_password)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def eml_with_attachment() -> bytes:
    msg = EmailMessage()
    msg["From"] = "Marie <marie@example.org>"
    msg["To"] = "luciano@example.org"
    msg["Subject"] = "Programme du week-end"
    msg["Date"] = "Mon, 21 Sep 2026 10:00:00 +0200"
    msg.set_content("Voici le programme.\nÀ bientôt !")
    msg.add_attachment(
        b"%PDF-1.4 fake",
        maintype="application",
        subtype="pdf",
        filename="programme.pdf",
    )
    return msg.as_bytes()


def png(width: int, height: int, color=(200, 80, 50)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def bmp(width: int = 10, height: int = 10) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (0, 0, 255)).save(buf, format="BMP")
    return buf.getvalue()


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def gmail_part(
    part_id,
    mime_type,
    filename="",
    *,
    attachment_id=None,
    data=None,
    size=None,
    disposition=None,
    content_id=None,
    parts=None,
):
    """Build a Gmail API payload part."""
    headers = []
    if disposition:
        headers.append(
            {
                "name": "Content-Disposition",
                "value": f'{disposition}; filename="{filename}"',
            }
        )
    if content_id:
        headers.append({"name": "Content-ID", "value": content_id})
    body = {"size": size if size is not None else (len(data) if data else 0)}
    if attachment_id:
        body["attachmentId"] = attachment_id
    if data is not None:
        body["data"] = b64url(data)
    part = {
        "partId": part_id,
        "mimeType": mime_type,
        "filename": filename,
        "headers": headers,
        "body": body,
    }
    if parts is not None:
        part["parts"] = parts
    return part


def gmail_message(message_id, parts, sender="Marie <marie@example.org>"):
    return {
        "id": message_id,
        "payload": {
            "partId": "",
            "mimeType": "multipart/mixed",
            "filename": "",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Date", "value": "Mon, 21 Sep 2026 10:00:00 +0200"},
                {"name": "Subject", "value": "Secret subject line"},
            ],
            "body": {"size": 0},
            "parts": parts,
        },
    }


def complex_message():
    """Nested multipart + forwarded e-mail + inline data + signature logo."""
    return gmail_message(
        "msg-1",
        [
            gmail_part(
                "0",
                "multipart/alternative",
                parts=[
                    gmail_part("0.0", "text/plain", data=b"Body text"),
                    gmail_part("0.1", "text/html", data=b"<p>Body</p>"),
                ],
            ),
            gmail_part(
                "1",
                "application/pdf",
                "rapport.pdf",
                attachment_id="ATT-PDF",
                size=12_000,
                disposition="attachment",
            ),
            gmail_part(
                "2",
                "text/csv",
                "liste.csv",
                data="nom;pays\nJoão;Brésil\n".encode(),
                disposition="attachment",
            ),
            gmail_part(
                "3",
                "image/png",
                "logo.png",
                attachment_id="ATT-LOGO",
                size=3_000,
                disposition="inline",
                content_id="<logo@x>",
            ),
            gmail_part(
                "4",
                "message/rfc822",
                "",
                parts=[
                    gmail_part(
                        "4.0",
                        "multipart/mixed",
                        parts=[
                            gmail_part("4.0.0", "text/plain", data=b"forwarded body"),
                            gmail_part(
                                "4.0.1",
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                "inner.docx",
                                attachment_id="ATT-INNER",
                                size=8_000,
                                disposition="attachment",
                            ),
                        ],
                    )
                ],
            ),
        ],
    )


def real_xlsx() -> bytes:
    """A workbook written by openpyxl: 3 named sheets, one hidden, typed cells."""
    import datetime as dt

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Inscriptions"
    ws.append(["Nom", "Pays", "Âge", "Arrivée", "Payé"])
    ws.append(["João", "Brésil", 23, dt.date(2027, 7, 26), True])
    ws.append(
        ['Marie, dite "Mimi"', "France", 19.5, dt.datetime(2027, 7, 27, 14, 30), False]
    )
    budget = wb.create_sheet("Budget")
    budget.append(["Poste", "Montant"])
    budget.append(["Transport", 1200.0])
    hidden = wb.create_sheet("Interne")
    hidden.append(["secret-ish"])
    hidden.sheet_state = "hidden"
    wb.create_sheet("Vide")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def pptx_with_notes() -> bytes:
    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    notes_type = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"
    )

    def slide(text):
        return (
            f'<?xml version="1.0"?><p:sld {_P} {_A}><p:cSld><p:spTree><p:sp><p:txBody>'
            f"<a:p><a:r><a:t>{text}</a:t></a:r></a:p>"
            '<a:p><a:fld type="slidenum"><a:t>99</a:t></a:fld></a:p>'
            "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
        )

    return _zip(
        {
            "ppt/presentation.xml": f'<?xml version="1.0"?><p:presentation {_P}/>',
            # slide10 sorts after slide2 numerically, not lexically.
            "ppt/slides/slide1.xml": slide("Accueil"),
            "ppt/slides/slide2.xml": slide("Programme"),
            "ppt/slides/slide10.xml": slide("Merci"),
            "ppt/slides/_rels/slide2.xml.rels": f'<?xml version="1.0"?><Relationships xmlns="{rel_ns}">'
            f'<Relationship Id="rId1" Type="{notes_type}" Target="../notesSlides/notesSlide7.xml"/>'
            "</Relationships>",
            "ppt/notesSlides/notesSlide7.xml": slide("Dire bonjour aux pèlerins"),
        }
    )
