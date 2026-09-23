import json
import logging
from typing import Any, Callable, Dict
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError
from mcp.types import ImageContent, TextContent

import cn_extras.drive_tools as dt
from cn_extras.drive_tools import ExportFailed, drive_read, strip_inline_images
from core.file_limits import FileTooLargeError
from core.server import server
from core.tool_registry import get_tool_components
from tests.cn_extras import fixtures as fx

async_test = pytest.mark.asyncio


def _unwrap(tool: Any) -> Callable[..., Any]:
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


read = _unwrap(drive_read)


def _text(result) -> str:
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent))


@pytest.fixture
def drive(monkeypatch):
    """Fake Drive: set .meta and .exports / .media, inspect .calls."""

    class Fake:
        meta: Dict = {}
        resolved_id: str = ""
        exports: Dict[str, Any] = {}
        media: Any = b""
        calls: list = []

    fake = Fake()
    fake.calls = []

    async def resolve(service, file_id, **_):
        return fake.resolved_id or file_id, dict(fake.meta)

    async def export(service, file_id, mime, limit):
        fake.calls.append(("export", mime))
        value = fake.exports[mime]
        if isinstance(value, Exception):
            raise value
        return value

    async def get_media(service, file_id, limit):
        fake.calls.append(("media", file_id))
        if isinstance(fake.media, Exception):
            raise fake.media
        return fake.media

    monkeypatch.setattr(dt, "resolve_drive_item", resolve)
    monkeypatch.setattr(dt, "_export", export)
    monkeypatch.setattr(dt, "_get_media", get_media)
    return fake


def _meta(mime, name="Doc", **extra):
    return {"id": "F1", "mimeType": mime, "name": name, **extra}


def test_drive_read_is_registered_read_only():
    tool = get_tool_components(server)["drive_read"]
    assert tool.annotations.readOnlyHint is True


class TestGoogleDocs:
    @async_test
    async def test_markdown_export_with_owner_as_source(self, drive):
        drive.meta = _meta(
            dt.GOOGLE_DOC, owners=[{"emailAddress": "marie@example.org"}]
        )
        drive.exports = {"text/markdown": b"# Titre\n\nTexte **gras**.\n"}
        text = _text(await read(Mock(), "u@x.org", file_id="F1"))
        assert "# Titre" in text and "**gras**" in text
        assert "untrusted, from: Drive file owned by marie@example.org" in text
        assert "Engine: drive-export" in text

    @async_test
    async def test_format_text_uses_plain_export(self, drive):
        drive.meta = _meta(dt.GOOGLE_DOC)
        drive.exports = {"text/plain": "﻿Plain text".encode()}
        text = _text(await read(Mock(), "u@x.org", file_id="F1", format="text"))
        assert "Plain text" in text
        assert drive.calls == [("export", "text/plain")]

    @async_test
    async def test_markdown_unavailable_falls_back_to_text(self, drive):
        drive.meta = _meta(dt.GOOGLE_DOC)
        drive.exports = {
            "text/markdown": ExportFailed("badRequest"),
            "text/plain": b"fallback",
        }
        text = _text(await read(Mock(), "u@x.org", file_id="F1"))
        assert "fallback" in text and "Markdown export unavailable (badRequest)" in text

    @async_test
    async def test_inline_base64_images_are_stripped(self, drive):
        drive.meta = _meta(dt.GOOGLE_DOC)
        big = "A" * 100_000
        drive.exports = {
            "text/markdown": f"Avant\n\n![logo JMJ](data:image/png;base64,{big})\n\nAprès".encode()
        }
        text = _text(await read(Mock(), "u@x.org", file_id="F1"))
        assert big not in text and "[image: logo JMJ]" in text
        assert "1 embedded image(s)" in text

    @async_test
    async def test_too_large_to_export_is_a_clear_error(self, drive):
        drive.meta = _meta(dt.GOOGLE_DOC, name="Énorme")
        drive.exports = {
            "text/markdown": ExportFailed("exportSizeLimitExceeded"),
            "text/plain": ExportFailed("exportSizeLimitExceeded"),
        }
        text = _text(await read(Mock(), "u@x.org", file_id="F1"))
        assert (
            text.startswith("Error:")
            and "exportSizeLimitExceeded" in text
            and "10 MB" in text
        )


def test_strip_inline_images_reference_style():
    md = "![chart][image1]\n\ntext\n\n[image1]: <data:image/png;base64,AAAA>\n"
    out, count = strip_inline_images(md)
    assert "data:image" not in out and "[image: chart]" in out and count == 1


class TestGoogleSheets:
    @async_test
    async def test_all_sheets_via_xlsx_export(self, drive):
        drive.meta = _meta(dt.GOOGLE_SHEET, name="Inscriptions JMJ")
        drive.exports = {dt.XLSX: fx.real_xlsx()}
        text = _text(await read(Mock(), "u@x.org", file_id="F1"))
        assert (
            "=== Sheet 1: Inscriptions ===" in text
            and "=== Sheet 2: Budget ===" in text
        )
        assert "Type: application/vnd.google-apps.spreadsheet" in text

    @async_test
    async def test_one_sheet_by_name(self, drive):
        drive.meta = _meta(dt.GOOGLE_SHEET)
        drive.exports = {dt.XLSX: fx.real_xlsx()}
        text = _text(await read(Mock(), "u@x.org", file_id="F1", sheet="Budget"))
        assert "Transport" in text and "João" not in text

    @async_test
    async def test_xlsx_export_refused_falls_back_to_first_sheet_csv(self, drive):
        drive.meta = _meta(dt.GOOGLE_SHEET)
        drive.exports = {
            dt.XLSX: ExportFailed("exportSizeLimitExceeded"),
            "text/csv": b"a,b\n1,2\n",
        }
        text = _text(await read(Mock(), "u@x.org", file_id="F1"))
        assert "a,b" in text and "only the FIRST sheet" in text


class TestOtherNativeTypes:
    @async_test
    async def test_slides_with_speaker_notes(self, drive):
        drive.meta = _meta(dt.GOOGLE_SLIDES)
        drive.exports = {dt.PPTX: fx.pptx_with_notes()}
        text = _text(await read(Mock(), "u@x.org", file_id="F1"))
        assert "--- slide 2 ---" in text and "Speaker notes: Dire bonjour" in text

    @async_test
    async def test_slides_pptx_refused_falls_back_to_text(self, drive):
        drive.meta = _meta(dt.GOOGLE_SLIDES)
        drive.exports = {
            dt.PPTX: ExportFailed("exportSizeLimitExceeded"),
            "text/plain": b"Slide text",
        }
        text = _text(await read(Mock(), "u@x.org", file_id="F1"))
        assert "Slide text" in text and "no speaker notes" in text

    @async_test
    async def test_drawing_is_an_image(self, drive):
        drive.meta = _meta(dt.GOOGLE_DRAWING)
        drive.exports = {"image/png": fx.png(50, 40)}
        result = await read(Mock(), "u@x.org", file_id="F1")
        assert any(isinstance(c, ImageContent) for c in result.content)

    @async_test
    async def test_folder_points_to_search(self, drive):
        drive.meta = _meta(dt.GOOGLE_FOLDER, name="Dossier")
        text = _text(await read(Mock(), "u@x.org", file_id="F1"))
        assert "is a folder" in text and "'F1' in parents" in text

    @async_test
    async def test_form_is_unsupported_with_reason(self, drive):
        drive.meta = _meta("application/vnd.google-apps.form")
        text = _text(await read(Mock(), "u@x.org", file_id="F1"))
        assert "Not readable" in text and "Forms" in text


class TestUploadedFiles:
    @async_test
    async def test_pdf_in_shared_drive(self, drive):
        drive.meta = _meta(
            "application/pdf", name="rapport.pdf", size="900", driveId="D1"
        )
        drive.media = fx.text_pdf("Rapport partagé")
        text = _text(await read(Mock(), "u@x.org", file_id="F1"))
        assert "Rapport partag" in text and "from: shared drive file" in text

    @async_test
    async def test_uploaded_xlsx_supports_sheet(self, drive):
        drive.meta = _meta(dt.XLSX, name="budget.xlsx", size="5000")
        drive.media = fx.real_xlsx()
        text = _text(await read(Mock(), "u@x.org", file_id="F1", sheet="2"))
        assert "=== Sheet 2: Budget ===" in text and "=== Sheet 1" not in text

    @async_test
    async def test_declared_size_over_limit_is_not_downloaded(self, drive, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", str(1024 * 1024))
        drive.meta = _meta("application/pdf", name="big.pdf", size=str(5 * 1024 * 1024))
        text = _text(await read(Mock(), "u@x.org", file_id="F1"))
        assert "too large (5 MB; limit 1 MB)" in text
        assert not drive.calls

    @async_test
    async def test_streaming_cap_hit_during_download(self, drive):
        drive.meta = _meta("application/pdf", name="x.pdf", size="10")
        drive.media = FileTooLargeError("raw upstream message with 'x.pdf'")
        text = _text(await read(Mock(), "u@x.org", file_id="F1"))
        assert "is too large (over the limit of" in text

    @async_test
    async def test_download_disabled_by_owner(self, drive):
        drive.meta = _meta("application/pdf", name="x.pdf", size="10")
        drive.media = ExportFailed("cannotDownloadFile")
        text = _text(await read(Mock(), "u@x.org", file_id="F1"))
        assert "cannotDownloadFile" in text and "owners can disable downloads" in text

    @async_test
    async def test_shortcut_is_followed_and_noted(self, drive):
        drive.meta = _meta("text/plain", name="notes.txt", size="5")
        drive.resolved_id = "TARGET"
        drive.media = b"hello"
        text = _text(await read(Mock(), "u@x.org", file_id="SHORTCUT"))
        assert "hello" in text and "Drive shortcut" in text
        assert drive.calls == [("media", "TARGET")]

    @async_test
    async def test_irrelevant_options_are_noted(self, drive):
        drive.meta = _meta("text/plain", name="a.txt", size="3")
        drive.media = b"abc"
        text = _text(
            await read(Mock(), "u@x.org", file_id="F1", format="text", sheet="2")
        )
        assert "format applies to Google Docs only" in text
        assert "sheet applies to spreadsheets only" in text

    @async_test
    async def test_info_logs_have_no_names_or_content(self, drive, caplog):
        caplog.set_level(logging.INFO)
        drive.meta = _meta(
            "text/plain",
            name="Projet secret.txt",
            size="20",
            owners=[{"emailAddress": "owner@x.org"}],
        )
        drive.media = b"contenu confidentiel"
        await read(Mock(), "u@x.org", file_id="F1")
        logged = " ".join(
            r.getMessage() for r in caplog.records if r.levelno >= logging.INFO
        )
        for secret in ("Projet secret", "contenu confidentiel", "owner@x.org"):
            assert secret not in logged


class TestExportHelper:
    """The real _export/_get_media, with download_media_bytes faked."""

    def _http_error(self, status, reason):
        body = json.dumps(
            {"error": {"errors": [{"reason": reason}], "code": status}}
        ).encode()
        return HttpError(Mock(status=status, reason="x"), body)

    @async_test
    async def test_export_maps_refusals_to_export_failed(self, monkeypatch):
        async def boom(*a, **k):
            raise self._http_error(403, "exportSizeLimitExceeded")

        monkeypatch.setattr(dt, "download_media_bytes", boom)
        with pytest.raises(ExportFailed) as info:
            await dt._export(Mock(), "F1", "text/markdown", 100)
        assert info.value.reason == "exportSizeLimitExceeded"

    @async_test
    async def test_server_errors_are_not_swallowed(self, monkeypatch):
        async def boom(*a, **k):
            raise self._http_error(500, "backendError")

        monkeypatch.setattr(dt, "download_media_bytes", boom)
        with pytest.raises(HttpError):
            await dt._export(Mock(), "F1", "text/markdown", 100)

    @async_test
    async def test_get_media_passes_supports_all_drives_and_limit(self, monkeypatch):
        seen = {}

        async def fake_download(request, **kwargs):
            seen.update(kwargs)
            return b"ok"

        monkeypatch.setattr(dt, "download_media_bytes", fake_download)
        service = Mock()
        assert await dt._get_media(service, "F1", 1234) == b"ok"
        service.files().get_media.assert_called_with(
            fileId="F1", supportsAllDrives=True
        )
        assert seen["max_bytes"] == 1234


class TestPhase2ReviewRegressions:
    def _http_error(self, status, reason):
        body = json.dumps(
            {"error": {"errors": [{"reason": reason}], "code": status}}
        ).encode()
        return HttpError(Mock(status=status, reason="x"), body)

    @pytest.mark.parametrize(
        "status,reason",
        [
            (403, "userRateLimitExceeded"),
            (403, "rateLimitExceeded"),
            (403, "insufficientPermissions"),
            (401, "authError"),
        ],
    )
    @async_test
    async def test_non_refusal_errors_propagate_from_export(
        self, monkeypatch, status, reason
    ):
        async def boom(*a, **k):
            raise self._http_error(status, reason)

        monkeypatch.setattr(dt, "download_media_bytes", boom)
        with pytest.raises(HttpError):
            await dt._export(Mock(), "F1", dt.XLSX, 100)

    @async_test
    async def test_bad_request_on_export_is_a_refusal(self, monkeypatch):
        async def boom(*a, **k):
            raise self._http_error(400, "badRequest")

        monkeypatch.setattr(dt, "download_media_bytes", boom)
        with pytest.raises(ExportFailed):
            await dt._export(Mock(), "F1", "text/markdown", 100)

    @async_test
    async def test_rate_limit_on_download_propagates(self, monkeypatch):
        async def boom(*a, **k):
            raise self._http_error(403, "userRateLimitExceeded")

        monkeypatch.setattr(dt, "download_media_bytes", boom)
        with pytest.raises(HttpError):
            await dt._get_media(Mock(), "F1", 100)

    @async_test
    async def test_our_byte_cap_on_markdown_falls_back_to_text(self, drive):
        drive.meta = _meta(dt.GOOGLE_DOC)
        drive.exports = {
            "text/markdown": FileTooLargeError("x"),
            "text/plain": b"small text",
        }
        text = _text(await read(Mock(), "u@x.org", file_id="F1"))
        assert "small text" in text and "download limit" in text

    @async_test
    async def test_our_byte_cap_on_xlsx_falls_back_and_notes_lost_sheet(self, drive):
        drive.meta = _meta(dt.GOOGLE_SHEET)
        drive.exports = {dt.XLSX: FileTooLargeError("x"), "text/csv": b"a,b\n"}
        text = _text(await read(Mock(), "u@x.org", file_id="F1", sheet="3"))
        assert (
            "only the FIRST sheet" in text and "sheet '3' could not be selected" in text
        )

    @async_test
    async def test_octet_stream_xlsx_sheet_is_not_reported_ignored(self, drive):
        drive.meta = _meta("application/octet-stream", name="budget.xlsx", size="5000")
        drive.media = fx.real_xlsx()
        text = _text(await read(Mock(), "u@x.org", file_id="F1", sheet="2"))
        assert "=== Sheet 2: Budget ===" in text
        assert "sheet applies to spreadsheets only" not in text


def test_reference_images_with_real_urls_are_kept():
    md = (
        "![photo][p1] and ![chart][c1]\n\n"
        "[p1]: https://example.org/p.png\n"
        "[c1]: <data:image/png;base64,AAAA>\n"
    )
    out, count = strip_inline_images(md)
    assert "![photo][p1]" in out and "[image: chart]" in out and count == 1


def test_image_regexes_do_not_span_lines_or_eat_paragraphs():
    md = "[note\n\n[x]: <data:image/png;base64,AA>\n\nNext paragraph\n"
    out, _ = strip_inline_images(md)
    assert "[note" in out and "Next paragraph" in out


def test_image_regexes_are_linear_on_hostile_input():
    import time

    started = time.monotonic()
    strip_inline_images("![" * 50_000 + "\n" + "![a][" * 20_000)
    assert time.monotonic() - started < 2
