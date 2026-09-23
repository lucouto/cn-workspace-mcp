import io
import zipfile

import pytest

from cn_extras.extract import extract, is_extractable, normalize_mime_type
from cn_extras.images import MAX_LONG_EDGE_PX
from tests.cn_extras import fixtures as fx


class TestPdf:
    def test_text_pdf(self):
        r = extract(fx.text_pdf("Hello World"), "application/pdf", "a.pdf")
        assert r.kind == "text" and r.engine == "local"
        assert "Hello World" in r.text
        assert "--- page 1 ---" in r.text
        assert r.page_count == 1

    def test_scanned_pdf_has_no_text_layer(self):
        r = extract(fx.blank_pdf(3), "application/pdf", "scan.pdf")
        assert r.kind == "unsupported"
        assert "scanned" in r.reason and "3 page" in r.reason
        assert r.page_count == 3

    def test_password_protected_pdf_is_rejected_clearly(self):
        r = extract(fx.encrypted_pdf("secret"), "application/pdf", "locked.pdf")
        assert r.kind == "unsupported"
        assert "password-protected" in r.reason

    def test_owner_password_only_pdf_opens(self):
        r = extract(
            fx.encrypted_pdf(user_password=""), "application/pdf", "noprint.pdf"
        )
        assert r.kind == "text"
        assert "Confidential" in r.text

    def test_corrupted_pdf(self):
        r = extract(b"%PDF-1.4\nthis is not a pdf", "application/pdf", "bad.pdf")
        assert r.kind == "unsupported"
        assert "damaged" in r.reason


class TestOffice:
    def test_docx_with_table_and_accents(self):
        r = extract(fx.docx_with_table(), fx.DOCX, "inscriptions.docx")
        assert r.kind == "text"
        for word in ("Inscriptions JMJ 2027", "João", "Brésil"):
            assert word in r.text

    def test_xlsx_without_openpyxl_falls_back_to_upstream(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def no_openpyxl(name, *args, **kwargs):
            if name.startswith("openpyxl") or name == "cn_extras.office":
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_openpyxl)
        r = extract(fx.xlsx_two_sheets(), fx.XLSX, "book.xlsx")
        assert "Sheet one value" in r.text and "Second sheet value" in r.text
        assert any("openpyxl not installed" in n for n in r.notes)

    def test_pptx(self):
        r = extract(fx.pptx_one_slide("Welcome to Paradise"), fx.PPTX, "deck.pptx")
        assert "Welcome to Paradise" in r.text
        assert "--- slide 1 ---" in r.text

    def test_corrupted_docx(self):
        r = extract(b"PK\x03\x04garbage", fx.DOCX, "bad.docx")
        assert r.kind == "unsupported"
        assert "damaged" in r.reason

    def test_office_member_over_limit(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_MAX_OFFICE_XML_BYTES", "50")
        r = extract(fx.docx_with_table(), fx.DOCX, "big.docx")
        assert r.kind == "unsupported"

    def test_legacy_doc(self):
        r = extract(b"\xd0\xcf\x11\xe0 legacy", "application/msword", "old.doc")
        assert r.kind == "unsupported" and ".docx" in r.reason


class TestText:
    def test_csv_utf8_with_bom(self):
        r = extract("﻿nom;ville\nJoão;São Paulo\n".encode("utf-8"), "text/csv", "l.csv")
        assert r.text.startswith("nom;ville")
        assert "São Paulo" in r.text
        assert not any("decoded as" in n for n in r.notes)

    def test_csv_windows_1252_fallback(self):
        r = extract("nom;ville\nJosé;Besançon\n".encode("cp1252"), "text/csv", "l.csv")
        assert "Besançon" in r.text
        assert any("cp1252" in n for n in r.notes)

    def test_binary_claiming_to_be_text(self):
        r = extract(b"abc\x00\x01\x02", "text/plain", "x.txt")
        assert r.kind == "unsupported"

    def test_html_drops_scripts_and_keeps_structure(self):
        html = (
            "<html><head><title>T</title><style>p{}</style></head><body>"
            "<script>alert('x')</script><h1>Titre</h1><p>Un&nbsp;paragraphe &amp; plus</p>"
            "<table><tr><td>a</td><td>b</td></tr></table></body></html>"
        )
        r = extract(html.encode(), "text/html", "page.html")
        assert "alert" not in r.text and "p{}" not in r.text
        assert "Titre" in r.text and "Un paragraphe & plus" in r.text
        assert "a\tb" in r.text

    def test_attached_email(self):
        r = extract(fx.eml_with_attachment(), "message/rfc822", "fwd.eml")
        assert "From: Marie <marie@example.org>" in r.text
        assert "Voici le programme." in r.text
        assert "programme.pdf" in r.text


class TestImages:
    def test_large_png_is_resized(self):
        r = extract(fx.png(3000, 1000), "image/png", "photo.png")
        assert r.kind == "image" and r.image_mime_type == "image/png"
        from PIL import Image

        assert max(Image.open(io.BytesIO(r.image_data)).size) == MAX_LONG_EDGE_PX
        assert any("resized" in n for n in r.notes)

    def test_small_png_passes_through_unchanged(self):
        data = fx.png(100, 100)
        r = extract(data, "image/png", "icon.png")
        assert r.image_data == data

    def test_bmp_is_converted_to_png(self):
        r = extract(fx.bmp(), "image/bmp", "old.bmp")
        assert r.kind == "image" and r.image_mime_type == "image/png"

    def test_corrupt_image(self):
        r = extract(b"\x89PNG\r\n\x1a\nnope", "image/png", "bad.png")
        assert r.kind == "unsupported"

    def test_without_pillow_native_image_passes_through(self, monkeypatch):
        import cn_extras.images as images

        monkeypatch.setattr(images, "_pillow", lambda: None)
        data = fx.png(100, 100)
        r = extract(data, "image/png", "icon.png")
        assert r.kind == "image" and r.image_data == data

    def test_without_pillow_bmp_is_unsupported(self, monkeypatch):
        import cn_extras.images as images

        monkeypatch.setattr(images, "_pillow", lambda: None)
        assert extract(fx.bmp(), "image/bmp", "old.bmp").kind == "unsupported"


class TestRoutingAndEdgeCases:
    def test_empty_file(self):
        assert extract(b"", "application/pdf", "e.pdf").kind == "unsupported"

    def test_zip_is_refused_with_reason(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.txt", "x")
        r = extract(buf.getvalue(), "application/zip", "a.zip")
        assert r.kind == "unsupported" and "ZIP" in r.reason

    def test_audio_video(self):
        assert extract(b"ID3...", "audio/mpeg", "a.mp3").kind == "unsupported"

    def test_octet_stream_is_resolved_from_filename(self):
        r = extract(
            fx.text_pdf("From extension"), "application/octet-stream", "doc.pdf"
        )
        assert r.kind == "text" and "From extension" in r.text

    def test_ocr_mode_falls_back_with_a_note(self):
        r = extract(fx.text_pdf(), "application/pdf", "a.pdf", mode="ocr")
        assert r.kind == "text"
        assert any("OCR" in n for n in r.notes)

    def test_invalid_mode(self):
        with pytest.raises(ValueError):
            extract(b"x", "text/plain", "a.txt", mode="magic")


class TestMimeHelpers:
    @pytest.mark.parametrize(
        "declared,filename,expected",
        [
            ("application/octet-stream", "a.docx", fx.DOCX),
            ("", "notes.md", "text/markdown"),
            ("text/plain", "list.csv", "text/csv"),
            ("text/plain", "evil.exe", "text/plain"),
            ("application/pdf; name=x.pdf", "x.pdf", "application/pdf"),
            ("APPLICATION/PDF", None, "application/pdf"),
            ("application/octet-stream", None, "application/octet-stream"),
        ],
    )
    def test_normalize(self, declared, filename, expected):
        assert normalize_mime_type(declared, filename) == expected

    @pytest.mark.parametrize(
        "mime,label",
        [
            ("application/pdf", "text"),
            ("image/jpeg", "image"),
            ("application/zip", "no"),
            (fx.XLSX, "text"),
        ],
    )
    def test_is_extractable(self, mime, label):
        assert is_extractable(mime) == label


class TestReviewRegressions:
    """Hostile or unusual inputs found in the Phase 1 review."""

    @pytest.mark.parametrize("fmt", ["ICO", "PPM", "TGA"])
    def test_other_pillow_formats_are_reencoded_not_mislabelled(self, fmt):
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (16, 16), (1, 2, 3)).save(buf, format=fmt)
        r = extract(buf.getvalue(), "image/png", f"x.{fmt.lower()}")
        assert r.kind == "image"
        assert r.image_mime_type == "image/png"
        assert r.image_data.startswith(b"\x89PNG")

    def test_pixel_bomb_is_rejected_before_decoding(self, monkeypatch):
        import cn_extras.images as images

        monkeypatch.setattr(images, "MAX_PIXELS", 10_000)
        r = extract(fx.png(200, 200), "image/png", "bomb.png")
        assert r.kind == "unsupported" and "too large" in r.reason

    def test_large_jpeg_is_downscaled(self):
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (4000, 3000), (10, 20, 30)).save(buf, format="JPEG")
        r = extract(buf.getvalue(), "image/jpeg", "photo.jpg")
        assert r.image_mime_type == "image/jpeg"
        assert max(Image.open(io.BytesIO(r.image_data)).size) <= MAX_LONG_EDGE_PX

    @pytest.mark.parametrize(
        "header",
        ["From: " + "(" * 5000 + "x@y", 'To: <>@,;:\\"[]', "From: =?utf-8?q?\x00?= <"],
    )
    def test_malformed_email_headers_do_not_crash(self, header):
        raw = f"{header}\r\nSubject: Hi\r\n\r\nBody still readable\r\n".encode(
            "utf-8", "surrogateescape"
        )
        r = extract(raw, "message/rfc822", "x.eml")
        assert r.kind == "text"
        assert "Body still readable" in r.text

    def test_parser_crash_becomes_unsupported(self, monkeypatch, caplog):
        import cn_extras.extract_local as local

        def boom(*_):
            raise RecursionError("deep")

        monkeypatch.setattr(local, "office_text", boom)
        r = extract(fx.docx_with_table(), fx.DOCX, "x.docx")
        assert r.kind == "unsupported" and "could not be parsed" in r.reason

    def test_pypdf_limit_errors_are_caught(self, monkeypatch):
        from pypdf.errors import LimitReachedError

        import pypdf

        def limited(*_a, **_k):
            raise LimitReachedError("too many objects")

        monkeypatch.setattr(pypdf, "PdfReader", limited)
        r = extract(fx.text_pdf(), "application/pdf", "x.pdf")
        assert r.kind == "unsupported" and "damaged" in r.reason

    def test_html_without_closing_head(self):
        r = extract(
            b"<html><head><title>t</title><body><p>Hello world</p>",
            "text/html",
            "x.html",
        )
        assert r.text == "Hello world"

    def test_html_unclosed_svg_does_not_swallow_page(self):
        r = extract(b"<p>Before</p><svg><p>After</p>", "text/html", "x.html")
        assert "Before" in r.text and "After" in r.text

    @pytest.mark.parametrize("encoding", ["utf-16", "utf-16-be"])
    def test_utf16_text_with_bom(self, encoding):
        text = "nom\tville\nJoão\tSão Paulo\n"
        data = text.encode(encoding)
        if encoding == "utf-16-be":
            data = b"\xfe\xff" + data
        r = extract(data, "text/plain", "export.txt")
        assert r.kind == "text" and "São Paulo" in r.text
