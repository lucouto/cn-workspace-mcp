"""Phase 2b: Document Intelligence fallback, routing, quota, page rendering."""

import io
import logging
from types import SimpleNamespace

import pytest

import cn_extras.extract_di as di
from cn_extras import heuristics, quota
from cn_extras.extract import OcrOptions, extract
from tests.cn_extras import fixtures as fx


@pytest.fixture(autouse=True)
def _clean_quota():
    quota.reset_for_tests()
    yield
    quota.reset_for_tests()


@pytest.fixture
def di_env(monkeypatch):
    monkeypatch.setenv("CN_DI_ENABLED", "true")
    monkeypatch.setenv("CN_DI_ENDPOINT", "https://example.cognitiveservices.azure.com/")
    monkeypatch.setenv("CN_DI_KEY", "test-key")


class FakePoller:
    def __init__(self, result, done=True, error=None):
        self.details = {"operation_id": "RESULT-1"}
        self._result, self._done, self._error = result, done, error

    def result(self, timeout=None):
        if self._error:
            raise self._error
        return self._result

    def done(self):
        return self._done


class FakeClient:
    def __init__(self, poller=None, begin_error=None, delete_error=None):
        self.poller, self.begin_error, self.delete_error = (
            poller,
            begin_error,
            delete_error,
        )
        self.begin_calls, self.deleted, self.closed = [], [], False

    def begin_analyze_document(self, model, body, **kwargs):
        self.begin_calls.append((model, body.read(), kwargs))
        if self.begin_error:
            raise self.begin_error
        return self.poller

    def delete_analyze_result(self, model, result_id):
        self.deleted.append((model, result_id))
        if self.delete_error:
            raise self.delete_error

    def close(self):
        self.closed = True


def _result(text="Texte OCR", pages=2):
    return SimpleNamespace(content=text, pages=[object()] * pages)


def _use(monkeypatch, client):
    monkeypatch.setattr(di, "_client", lambda: client)
    return client


# --- extract_di.analyze -------------------------------------------------------


class TestAnalyze:
    def test_success_sends_bytes_and_always_deletes(self, di_env, monkeypatch):
        client = _use(monkeypatch, FakeClient(FakePoller(_result())))
        out = di.analyze(b"%PDF bytes", di.READ_MODEL, "1-2")
        assert out.text == "Texte OCR" and out.pages_analyzed == 2
        model, body, kwargs = client.begin_calls[0]
        assert model == "prebuilt-read" and body == b"%PDF bytes"
        assert kwargs["pages"] == "1-2" and "output_content_format" not in kwargs
        assert client.deleted == [("prebuilt-read", "RESULT-1")] and client.closed

    def test_layout_asks_for_markdown(self, di_env, monkeypatch):
        client = _use(monkeypatch, FakeClient(FakePoller(_result())))
        di.analyze(b"x", di.LAYOUT_MODEL, None)
        kwargs = client.begin_calls[0][2]
        assert str(kwargs["output_content_format"].value) == "markdown"
        assert "pages" not in kwargs

    def test_service_error_still_deletes_and_hides_details(self, di_env, monkeypatch):
        from azure.core.exceptions import HttpResponseError

        client = _use(
            monkeypatch,
            FakeClient(FakePoller(None, error=HttpResponseError("secret detail"))),
        )
        with pytest.raises(di.DiFailed) as info:
            di.analyze(b"x", di.READ_MODEL, None)
        assert "secret detail" not in str(info.value)
        assert client.deleted == [("prebuilt-read", "RESULT-1")]

    def test_timeout_is_a_failure_and_still_attempts_delete(self, di_env, monkeypatch):
        monkeypatch.setenv("CN_DI_TIMEOUT_S", "1")
        client = _use(monkeypatch, FakeClient(FakePoller(None, done=False)))
        with pytest.raises(di.DiFailed, match="within 1 s"):
            di.analyze(b"x", di.READ_MODEL, None)
        assert client.deleted

    def test_unexpected_exception_is_a_clean_failure(self, di_env, monkeypatch):
        _use(monkeypatch, FakeClient(begin_error=KeyError("Operation-Location")))
        with pytest.raises(di.DiFailed, match="KeyError"):
            di.analyze(b"x", di.READ_MODEL, None)

    def test_failed_cleanup_is_logged_not_raised(self, di_env, monkeypatch, caplog):
        _use(
            monkeypatch,
            FakeClient(FakePoller(_result()), delete_error=RuntimeError("boom")),
        )
        caplog.set_level(logging.WARNING)
        assert di.analyze(b"x", di.READ_MODEL, None).text == "Texte OCR"
        assert "delete_analyze_result failed" in caplog.text

    def test_no_network_when_disabled_or_too_big(self, di_env, monkeypatch):
        client = _use(monkeypatch, FakeClient(FakePoller(_result())))
        monkeypatch.setenv("CN_DI_MAX_BYTES", "3")
        with pytest.raises(di.DiUnavailable, match="size limit"):
            di.analyze(b"abcd", di.READ_MODEL, None)
        monkeypatch.setenv("CN_DI_ENABLED", "false")
        with pytest.raises(di.DiUnavailable, match="not enabled"):
            di.analyze(b"a", di.READ_MODEL, None)
        assert client.begin_calls == []


class TestStatus:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("CN_DI_ENABLED", raising=False)
        assert "not enabled" in di.status()

    def test_enabled_without_credentials(self, monkeypatch):
        monkeypatch.setenv("CN_DI_ENABLED", "true")
        monkeypatch.delenv("CN_DI_KEY", raising=False)
        assert "not configured" in di.status()

    def test_ready(self, di_env):
        assert di.status() is None


class TestParsePages:
    @pytest.mark.parametrize(
        "spec,count,expected",
        [
            (None, 3, ("1-3", [1, 2, 3])),
            (None, 100, ("1-20", list(range(1, 21)))),
            ("2,4-5", 10, ("2,4-5", [2, 4, 5])),
            (" 3 - 4 , 3 ", 10, ("3-4", [3, 4])),
            ("5-9", 6, ("5-6", [5, 6])),
        ],
    )
    def test_valid(self, spec, count, expected):
        assert di.parse_pages(spec, count) == expected

    @pytest.mark.parametrize("spec", ["abc", "0", "5-2", "1-100000", "1-25"])
    def test_invalid(self, spec):
        with pytest.raises(ValueError):
            di.parse_pages(spec, None)

    def test_beyond_document(self):
        with pytest.raises(ValueError, match="no pages"):
            di.parse_pages("8-9", 3)


# --- heuristics and quota --------------------------------------------------------


class TestHeuristics:
    def test_scanned_threshold(self, monkeypatch):
        assert heuristics.is_scanned(["", "", "x" * 60])
        assert not heuristics.is_scanned(["x" * 200, "y" * 200])
        monkeypatch.setenv("CN_DI_SCANNED_CHARS_PER_PAGE", "500")
        assert heuristics.is_scanned(["x" * 200, "y" * 200])

    def test_garbled_table_detected(self):
        # 8 gaps per wide line: 12*8/22 = 4.4 runs per line (> 3); 10/22 short (> 0.4).
        wide = "Nom   Pays   Ville   Age   Tel   Mail   Date   Prix   Total"
        text = "\n".join([wide] * 12 + ["12"] * 10)
        assert heuristics.looks_garbled(text)

    def test_normal_prose_and_short_lists_are_not_garbled(self):
        prose = "\n".join(["Ceci est une phrase ordinaire d'un rapport annuel."] * 30)
        items = "\n".join(["- pain", "- lait"] * 15)
        assert not heuristics.looks_garbled(prose)
        assert not heuristics.looks_garbled(items)
        assert not heuristics.looks_garbled(
            "a   b   c   d   e\nx\n" * 3
        )  # too few lines


class TestQuota:
    def test_reserve_settle_and_limit(self, monkeypatch):
        monkeypatch.setenv("CN_DI_DAILY_PAGES_PER_USER", "10")
        key = quota.reserve("a@x", 8)
        quota.settle(key, 8, 3)  # only 3 were billed
        quota.reserve("a@x", 7)
        with pytest.raises(quota.QuotaExceeded, match="10 of 10"):
            quota.reserve("a@x", 1)
        quota.reserve("b@x", 10)  # per user

    def test_logs_totals_without_content(self, caplog):
        caplog.set_level(logging.INFO)
        key = quota.reserve("a@x", 2)
        quota.settle(key, 2, 2)
        assert "user_today=2" in caplog.text and "all_users_today=2" in caplog.text

    def test_call_crossing_midnight_bills_the_new_day_once(self, monkeypatch):
        days = iter(["2026-09-23", "2026-09-24", "2026-09-24", "2026-09-24"])
        monkeypatch.setattr(quota, "_today", lambda: next(days))
        key = quota.reserve("a@x", 5)  # 23rd
        quota.settle(key, 5, 4)  # settles on the 24th
        assert quota._used.get(("a@x", "2026-09-24")) == 4
        assert quota._used.get(("a@x", "2026-09-23"), 0) == 0


# --- routing through extract() ----------------------------------------------------


@pytest.fixture
def fake_ocr(di_env, monkeypatch):
    calls = []

    def analyze(data, model, pages):
        calls.append((model, pages))
        billed = len(di.parse_pages(pages, None)[1]) if pages else 1
        return di.DiResult(
            text=f"OCR text via {model}", model=model, pages_analyzed=billed
        )

    monkeypatch.setattr(di, "analyze", analyze)
    return calls


def _opts(**kw):
    return OcrOptions(user="u@x.org", **kw)


class TestRouting:
    def test_scanned_pdf_goes_to_read_model(self, fake_ocr):
        r = extract(fx.blank_pdf(3), "application/pdf", "scan.pdf", ocr=_opts())
        assert r.kind == "text" and r.engine == "di-read"
        assert fake_ocr == [("prebuilt-read", "1-3")]
        assert any("no usable text layer" in n for n in r.notes)

    def test_text_pdf_stays_local(self, fake_ocr):
        r = extract(
            fx.text_pdf("Local text " * 10), "application/pdf", "a.pdf", ocr=_opts()
        )
        assert r.engine == "local" and fake_ocr == []

    def test_mode_ocr_forces_layout(self, fake_ocr):
        r = extract(
            fx.text_pdf(), "application/pdf", "a.pdf", mode="ocr", ocr=_opts(pages="1")
        )
        assert r.engine == "di-layout" and fake_ocr == [("prebuilt-layout", "1")]

    def test_garbled_table_goes_to_layout(self, fake_ocr, monkeypatch):
        monkeypatch.setattr(heuristics, "looks_garbled", lambda text: True)
        r = extract(
            fx.text_pdf("Some text " * 10), "application/pdf", "t.pdf", ocr=_opts()
        )
        assert r.engine == "di-layout" and "garbled" in " ".join(r.notes)

    def test_mode_local_never_calls_ocr(self, fake_ocr):
        r = extract(
            fx.blank_pdf(), "application/pdf", "s.pdf", mode="local", ocr=_opts()
        )
        assert r.kind == "unsupported" and fake_ocr == []
        assert "render_pages=true" in r.reason

    def test_encrypted_pdf_is_never_sent(self, fake_ocr):
        r = extract(
            fx.encrypted_pdf("pw"), "application/pdf", "l.pdf", mode="ocr", ocr=_opts()
        )
        assert r.kind == "unsupported" and fake_ocr == []

    def test_quota_is_charged_and_enforced(self, fake_ocr, monkeypatch):
        monkeypatch.setenv("CN_DI_DAILY_PAGES_PER_USER", "3")
        extract(fx.blank_pdf(3), "application/pdf", "s.pdf", ocr=_opts(pages="1"))
        extract(fx.blank_pdf(3), "application/pdf", "s.pdf", ocr=_opts(pages="1-2"))
        r = extract(fx.blank_pdf(3), "application/pdf", "s.pdf", ocr=_opts(pages="1"))
        assert r.kind == "unsupported" and "daily OCR quota" in " ".join(r.notes)
        assert len(fake_ocr) == 2

    def test_invalid_pages_is_a_clear_error(self, fake_ocr):
        r = extract(fx.blank_pdf(), "application/pdf", "s.pdf", ocr=_opts(pages="zz"))
        assert r.kind == "unsupported" and "invalid page range" in r.reason

    def test_pages_on_text_pdf_is_noted_not_an_error(self, fake_ocr):
        r = extract(
            fx.text_pdf("Local text " * 10),
            "application/pdf",
            "a.pdf",
            ocr=_opts(pages="zz"),
        )
        assert r.kind == "text" and any("pages applies to OCR" in n for n in r.notes)

    def test_ocr_failure_falls_back_to_local_text(self, di_env, monkeypatch):
        def failing(*a):
            raise di.DiFailed("OCR did not finish within 60 s")

        monkeypatch.setattr(di, "analyze", failing)
        r = extract(
            fx.text_pdf("Still here " * 10),
            "application/pdf",
            "a.pdf",
            mode="ocr",
            ocr=_opts(),
        )
        assert r.engine == "local" and "Still here" in r.text
        assert any("did not finish" in n for n in r.notes)

    def test_empty_ocr_text_counts_as_not_used(self, di_env, monkeypatch):
        monkeypatch.setattr(di, "analyze", lambda *a: di.DiResult("", di.READ_MODEL, 1))
        r = extract(fx.blank_pdf(), "application/pdf", "s.pdf", ocr=_opts())
        assert r.kind == "unsupported" and any("found no text" in n for n in r.notes)


class TestFallbacksWithoutOcr:
    def test_disabled_scanned_pdf_explains_and_suggests_rendering(self, monkeypatch):
        monkeypatch.delenv("CN_DI_ENABLED", raising=False)
        r = extract(fx.blank_pdf(2), "application/pdf", "s.pdf", ocr=_opts())
        assert r.kind == "unsupported" and "render_pages=true" in r.reason
        assert any("not enabled" in n for n in r.notes)

    def test_render_pages_returns_real_page_images(self, monkeypatch):
        monkeypatch.delenv("CN_DI_ENABLED", raising=False)
        r = extract(
            fx.blank_pdf(2), "application/pdf", "s.pdf", ocr=_opts(render_pages=True)
        )
        assert r.kind == "image" and r.engine == "page-images" and len(r.images) == 2
        from PIL import Image

        for image in r.images:
            assert image.mime_type == "image/jpeg"
            assert max(Image.open(io.BytesIO(image.data)).size) <= 1568

    def test_render_cap_and_page_selection(self, monkeypatch):
        monkeypatch.delenv("CN_DI_ENABLED", raising=False)
        monkeypatch.setenv("CN_RENDER_MAX_PAGES", "2")
        r = extract(
            fx.blank_pdf(6),
            "application/pdf",
            "s.pdf",
            ocr=_opts(pages="3-6", render_pages=True),
        )
        assert len(r.images) == 2 and any("page(s) 3-4 of 6" in n for n in r.notes)

    def test_garbled_suspicion_without_ocr_is_quiet(self, monkeypatch):
        monkeypatch.delenv("CN_DI_ENABLED", raising=False)
        monkeypatch.setattr(heuristics, "looks_garbled", lambda text: True)
        r = extract(
            fx.text_pdf("Some text " * 10), "application/pdf", "t.pdf", ocr=_opts()
        )
        assert r.engine == "local" and not any("OCR not used" in n for n in r.notes)


class TestImages:
    def _tiff(self, frames: int) -> bytes:
        from PIL import Image

        buf = io.BytesIO()
        pages = [Image.new("RGB", (20, 20), (i * 40, 0, 0)) for i in range(frames)]
        pages[0].save(buf, format="TIFF", save_all=True, append_images=pages[1:])
        return buf.getvalue()

    def test_plain_image_is_not_ocrd_in_auto(self, fake_ocr):
        r = extract(fx.png(50, 50), "image/png", "p.png", ocr=_opts())
        assert r.kind == "image" and fake_ocr == []

    def test_image_with_mode_ocr(self, fake_ocr):
        r = extract(fx.png(50, 50), "image/png", "receipt.png", mode="ocr", ocr=_opts())
        assert r.engine == "di-layout" and fake_ocr == [("prebuilt-layout", None)]

    def test_multipage_tiff_goes_to_ocr_with_pages(self, fake_ocr):
        r = extract(self._tiff(3), "image/tiff", "fax.tiff", ocr=_opts())
        assert r.engine == "di-read" and fake_ocr == [("prebuilt-read", "1-3")]

    def test_multipage_tiff_without_ocr_shows_first_page(self, monkeypatch):
        monkeypatch.delenv("CN_DI_ENABLED", raising=False)
        r = extract(self._tiff(3), "image/tiff", "fax.tiff", ocr=_opts())
        assert r.kind == "image" and any(
            "3 pages; only the first" in n for n in r.notes
        )

    def test_ocr_mode_on_docx_is_noted(self, fake_ocr):
        r = extract(fx.docx_with_table(), fx.DOCX, "d.docx", mode="ocr", ocr=_opts())
        assert r.engine == "local" and fake_ocr == []
        assert any("applies to PDFs and images" in n for n in r.notes)


class TestThroughTheTool:
    @staticmethod
    def _read(svc, **kw):
        from cn_extras.tools import gmail_read_attachment

        fn = (
            gmail_read_attachment.fn
            if hasattr(gmail_read_attachment, "fn")
            else gmail_read_attachment
        )
        while hasattr(fn, "__wrapped__"):
            fn = fn.__wrapped__
        return fn(svc, "u@x.org", message_id="m", part_id="1", **kw)

    @staticmethod
    def _service(pdf: bytes):
        from unittest.mock import Mock

        msg = fx.gmail_message(
            "m",
            [
                fx.gmail_part(
                    "1", "application/pdf", "scan.pdf", attachment_id="A", size=len(pdf)
                )
            ],
        )
        svc = Mock()
        svc.users().messages().get().execute.return_value = msg
        svc.users().messages().attachments().get().execute.return_value = {
            "data": fx.b64url(pdf)
        }
        return svc

    @pytest.mark.asyncio
    async def test_render_pages_returns_one_image_per_page(self, monkeypatch):
        from mcp.types import ImageContent, TextContent

        monkeypatch.delenv("CN_DI_ENABLED", raising=False)
        result = await self._read(self._service(fx.blank_pdf(3)), render_pages=True)
        images = [c for c in result.content if isinstance(c, ImageContent)]
        header = "".join(c.text for c in result.content if isinstance(c, TextContent))
        assert len(images) == 3 and "Engine: page-images" in header
        assert "untrusted image from: Marie" in header

    @pytest.mark.asyncio
    async def test_ocr_is_charged_to_the_calling_user(self, fake_ocr, monkeypatch):
        seen = []
        real_reserve = quota.reserve
        monkeypatch.setattr(
            quota, "reserve", lambda user, n: (seen.append(user), real_reserve(user, n))
        )
        result = await self._read(self._service(fx.blank_pdf(2)), pages="2")
        text = result.content[0].text
        assert "OCR text via prebuilt-read" in text and "Engine: di-read" in text
        assert seen == ["u@x.org"] and fake_ocr == [("prebuilt-read", "2")]


def test_missing_sdk_is_reported_and_extraction_still_works(di_env, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_azure(name, *args, **kwargs):
        if name.startswith("azure"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_azure)
    assert "not installed" in di.status()
    r = extract(fx.blank_pdf(), "application/pdf", "s.pdf", ocr=OcrOptions(user="u"))
    assert r.kind == "unsupported" and any("not installed" in n for n in r.notes)
    assert (
        extract(fx.text_pdf("fine " * 20), "application/pdf", "a.pdf").engine == "local"
    )


class TestPhase2bReviewRegressions:
    # 1 — pdfium is never entered by two threads at once
    def test_rendering_is_serialised_across_threads(self, monkeypatch):
        import threading
        import time

        import cn_extras.pages as pages

        active, peak, lock = [0], [0], threading.Lock()
        real = pages._render_locked

        def instrumented(*args):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.02)
            try:
                return real(*args)
            finally:
                with lock:
                    active[0] -= 1

        monkeypatch.setattr(pages, "_render_locked", instrumented)
        pdf = fx.blank_pdf(1)
        errors = []

        def work():
            try:
                pages.render_pages(pdf, [1])
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors and peak[0] == 1

    # 2 — timeout: result deleted later by the done-callback, client kept open until then
    def test_timeout_defers_cleanup_to_completion(self, di_env, monkeypatch):
        class SlowPoller(FakePoller):
            def __init__(self):
                super().__init__(None, done=False)
                self.callbacks = []

            def add_done_callback(self, fn):
                self.callbacks.append(fn)

        poller = SlowPoller()
        client = _use(monkeypatch, FakeClient(poller))
        with pytest.raises(di.DiFailed, match="did not finish"):
            di.analyze(b"x", di.READ_MODEL, None)
        assert client.deleted == [] and not client.closed
        for callback in poller.callbacks:  # the SDK thread finishing later
            callback(None)
            callback(None)  # idempotent
        assert client.deleted == [("prebuilt-read", "RESULT-1")] and client.closed

    def test_timeout_without_callback_support_cleans_up_immediately(
        self, di_env, monkeypatch
    ):
        client = _use(monkeypatch, FakeClient(FakePoller(None, done=False)))
        with pytest.raises(di.DiFailed):
            di.analyze(b"x", di.READ_MODEL, None)
        assert client.deleted and client.closed

    # 3 — no resend after a read timeout, few retries overall
    def test_client_retry_policy(self, di_env, monkeypatch):
        import azure.ai.documentintelligence as sdk

        seen = {}

        class Capture:
            def __init__(self, endpoint, credential, **kwargs):
                seen.update(kwargs)

        monkeypatch.setattr(sdk, "DocumentIntelligenceClient", Capture)
        di._client()
        assert seen["retry_read"] == 0 and seen["retry_total"] <= 2
        assert seen["read_timeout"] == 60

    # 4 — TIFF with unknown frame count is still bounded and reserved in full
    @pytest.mark.filterwarnings("ignore:Corrupt EXIF data")
    def test_unreadable_tiff_is_bounded(self, fake_ocr, monkeypatch):
        import cn_extras.extract as ex

        monkeypatch.setattr(ex, "_frame_count", lambda data: None)
        seen = []
        real = quota.reserve
        monkeypatch.setattr(
            quota, "reserve", lambda u, n: (seen.append(n), real(u, n))[1]
        )
        r = extract(b"II*\x00garbage", "image/tiff", "fax.tif", mode="ocr", ocr=_opts())
        assert r.kind == "text"
        assert fake_ocr == [("prebuilt-layout", "1-20")] and seen == [20]

    # 5 — paginating an OCR result warns about re-billing
    def test_ocr_pagination_warns_about_rebilling(self):
        from mcp.types import TextContent

        from cn_extras.extract import ExtractResult
        from cn_extras.render import render_result

        r = ExtractResult(
            kind="text", mime_type="application/pdf", engine="di-read", text="x" * 500
        )
        out = render_result(r, filename="s.pdf", source="s", max_chars=100)
        text = "".join(c.text for c in out.content if isinstance(c, TextContent))
        assert "re-runs OCR" in text
        local = ExtractResult(
            kind="text", mime_type="application/pdf", engine="local", text="x" * 500
        )
        text = "".join(
            c.text
            for c in render_result(
                local, filename="s", source="s", max_chars=100
            ).content
        )
        assert "re-runs OCR" not in text

    # 6 — mixed PDFs: OCR the pages without text, keep the others' text layer
    def test_mixed_pdf_ocrs_thin_pages_and_keeps_text_layer(self, fake_ocr):
        r = extract(fx.mixed_pdf("TSTSS"), "application/pdf", "mix.pdf", ocr=_opts())
        assert fake_ocr == [("prebuilt-read", "2,4-5")]
        assert "OCR text via prebuilt-read" in r.text
        assert "Pages not OCR'd" in r.text
        assert "Page 1 texte reel" in r.text and "Page 3 texte reel" in r.text

    def test_long_mixed_pdf_keeps_text_beyond_the_ocr_window(self, fake_ocr):
        layout = "S" * 25 + "T" * 3  # 25 scans then 3 text pages
        r = extract(fx.mixed_pdf(layout), "application/pdf", "long.pdf", ocr=_opts())
        assert fake_ocr == [("prebuilt-read", "1-20")]
        assert "Page 26 texte reel" in r.text
        assert any("5 page(s) without text were not OCR'd" in n for n in r.notes)

    # 7 — formats DI doesn't accept are never sent
    @pytest.mark.parametrize("fmt,mime", [("GIF", "image/gif"), ("WEBP", "image/webp")])
    def test_gif_webp_are_not_sent_to_ocr(self, fake_ocr, fmt, mime):
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (20, 20)).save(buf, format=fmt)
        r = extract(buf.getvalue(), mime, "x", mode="ocr", ocr=_opts())
        assert fake_ocr == [] and r.kind == "image"
        assert any("OCR does not support" in n for n in r.notes)

    # 8 — bad OCR config is a clean fallback, not "could not be parsed"
    def test_misconfigured_timeout_falls_back_cleanly(self, di_env, monkeypatch):
        monkeypatch.setenv("CN_DI_TIMEOUT_S", "soon")
        r = extract(fx.blank_pdf(), "application/pdf", "s.pdf", ocr=_opts())
        assert "could not be parsed" not in (r.reason or "")
        assert any("misconfigured" in n for n in r.notes)

    # 10 — long page specs are rejected quickly
    def test_long_page_spec_is_rejected_fast(self):
        import time

        started = time.monotonic()
        # 20000 distinct pages: quadratic de-duplication would take seconds.
        heavy = ",".join(str(i) for i in range(1, 20_000))
        for spec in (heavy, ",".join(str(i) for i in range(1, 60))):
            with pytest.raises(ValueError):
                di.parse_pages(spec, None)
        assert time.monotonic() - started < 0.5


def test_azure_http_logging_is_quiet():
    import logging

    import cn_extras.extract_di  # noqa: F401

    http_log = logging.getLogger("azure.core.pipeline.policies.http_logging_policy")
    assert not http_log.isEnabledFor(logging.INFO)
