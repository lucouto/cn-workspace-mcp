# LOOP-STATE

## Phase 2b — Document Intelligence fallback (code only)

Spec: `docs/PLAN.md` §5.6 (+ as-built notes). No Azure resource yet: everything is tested against a fake client that mirrors the SDK signatures read from azure-ai-documentintelligence 1.0.2 / azure-core 1.41.0.

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | `heuristics.py` — scanned + garbled-table metrics (env thresholds) | done | |
| 2 | `quota.py` — per-user daily pages, reserve/settle, totals logged | done | in-memory; Valkey if multi-replica |
| 3 | `extract_di.py` — status, page-range parsing/cap, analyze with delete in `finally`, timeout, size cap | done | |
| 4 | `pages.py` — pypdfium2 rendering, long edge ≤ 1568 px | done | |
| 5 | Routing in `extract()`, multi-image results, tool params `pages`/`render_pages` | done | |
| 6 | Tests (`test_ocr.py`) incl. tool-level and missing-SDK | done | |
| 7 | Independent reviewer | done | 11 findings (3 high); 10 fixed with regression tests, #11 (no render time limit) mitigated by the lock + 5-page cap. New tests fail on pre-fix code except 1 intended baseline. |

### Phase 2b review fixes
- HIGH pdfium thread-safety → process-wide lock. HIGH timeout left results undeleted and closed the client under the SDK's poller → deferred delete via `add_done_callback`. HIGH retries could exceed the timeout and double-bill → `retry_read=0`, bounded total.
- TIFF quota/page-cap bypass; OCR re-billing on pagination (noted); mixed PDFs dropping text; GIF/WebP sent to DI; config errors surfacing as parse errors; quota across midnight; O(n²) page-spec parsing.
- Accepted: a hostile page can hold one render slot for a while (bounded memory, 5 pages, serialised).

### Needs validation (Luciano)
- OCR text cache: every `offset` page of an OCR result re-bills OCR. Option: short-lived (≤24 h) encrypted in-memory cache of the *extracted text* keyed by user + file + pages (PLAN §5.6 said "decide after the pilot"). Recommendation: decide after seeing real pilot usage.
- Create the Azure Document Intelligence resource (EU region, F0 for dev, S0 for the integration tests), put endpoint/key in Coolify secrets, set the budget alert (PLAN §8). Then run the §9 DI integration set and tune thresholds.
- Monthly DI budget → sets `CN_DI_DAILY_PAGES_PER_USER`.


## Phase 2 — Drive tool (`drive_read`)

Spec: `docs/PLAN.md` §5.3 (+ as-built notes). Verify: `uv run pytest -q` + `uv run ruff check .` + stdio tools/list + independent reviewer.

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | `cn_extras/office.py` — XLSX per sheet as CSV (openpyxl, read-only, sheet selection, hidden flag, typed cells), PPTX slides + speaker notes; zip-size guard, output cap | done | also used for Gmail/Drive uploaded .xlsx/.pptx; upstream fallback without openpyxl |
| 2 | `cn_extras/render.py` — shared result rendering (text/image/unsupported) | done | gmail_read_attachment refactored onto it |
| 3 | `cn_extras/drive_tools.py` — `drive_read`: Docs md→text, Sheets XLSX→CSV fallback, Slides PPTX→text fallback, Drawings PNG, folders/other native, uploaded files, shortcuts, shared drives, refusals | done | |
| 4 | Tests (`test_office.py`, `test_drive_tools.py`, fixtures) | done | 2564 passed / 2 skipped |
| 5 | Docs: PLAN as-built §5.3, FORK_CHANGES | done | |
| 6 | Independent reviewer | done | 7 findings, all fixed with regression tests; each new test fails on pre-fix code except 2 intended baselines. Sparse-sheet test hangs the old code. |

### Phase 2 review fixes
- HIGH: sparse XLSX (A1 + XFD1048576, 4.8 KB) → ~14 min CPU. Fixed: `reset_dimensions()` (0.16 s) + 5 M cell-visit budget (wide rows).
- Only allowlisted reasons are refusals; rate limits/permissions/auth propagate to upstream's handler.
- Per-part limit (upstream 25 MiB) on in-memory-parsed parts; package total 256 MB; worksheets streamed.
- Our byte cap on rich exports falls back to the smaller export.
- Lost `sheet` selection on CSV fallback is noted.
- Markdown image regexes: bounded, single-line, only data-URI references replaced, linear time.
- "sheet ignored" note uses the filename-normalised type.

## Needs validation
- (none)

## Carry-over notes
- **Unverified against live Google:** Docs `text/markdown` export support and its exact image syntax; real `exportSizeLimitExceeded` reason string; XLSX exports containing cached formula values (openpyxl `data_only=True` relies on them). Check in the Phase 5 pilot with real files; code falls back safely in each case.
- Deploy (Phase 4): Dockerfile must `uv sync --extra cn`, and `WORKSPACE_MCP_TOOLS=gmail,drive,cn`.
- DI fallback (`mode="ocr"`, page rendering with pypdfium2) is Phase 2b.

---

## Phase 1 — extraction + Gmail tools (done, `cn/phase-1`)

Verify after each item: `uv run pytest tests/cn_extras -q` + `uv run ruff check cn_extras tests/cn_extras`.
Final gate: full `uv run pytest -q` + `uv run ruff check .` + independent reviewer agent.

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | `cn_extras/mime.py` — recursive MIME walker (attachmentId + inline `body.data`, rfc822, partId, disposition, signature-image flag) | done | |
| 2 | `cn_extras/output.py` — pagination (`offset`/`max_chars`), header, untrusted-content wrapper | done | |
| 3 | `cn_extras/extract_local.py` + `extract.py` — PDF/Office via upstream, CSV/TXT (UTF-8 → cp1252 fallback), HTML → text, images, unsupported/encrypted | done | `mode` param accepted; `ocr` → "not available until Phase 2b" |
| 4 | `cn_extras/images.py` — Pillow resize to ≤1568 px long edge, graceful fallback without Pillow | done | |
| 5 | `cn_extras/tools.py` — `gmail_list_attachments`, `gmail_read_attachment`, `whoami` | done | part_id is the stable key (attachment IDs rotate) |
| 6 | Upstream touch: `main.py` SERVICE_MODULES `"cn"`; `pyproject.toml` extra `cn` (Pillow) | done | log in FORK_CHANGES §3 |
| 7 | Fixtures + tests (`tests/cn_extras/`) | done | fixtures generated in code, no binary blobs |
| 8 | Full suite + ruff + reviewer agent | done | 2521 passed / 2 skipped; ruff clean; stdio tools/list shows exactly the 6 intended tools. Reviewer: 9 findings, all fixed with regression tests (23/28 fail on pre-fix code; the rest are baselines). |

## Needs validation (decisions for Luciano)
- ~~Listing omits Gmail `attachment_id`s, keyed on `part_id`~~ → **confirmed by Luciano** (23 Sept 2026).

## Review fixes (Phase 1 reviewer agent)
- Images: bytes/MIME mismatch for non-native Pillow formats (ICO/PPM/TGA/MPO) → always re-encode; pixel-bomb cap (40 MP) checked before decode; JPEG draft decode; no copy.
- Untrusted boundary: per-call nonce in both markers + neutralise look-alikes (case, full-width bracket, zero-width). No NFKC (would alter content).
- Parser crashes (email header IndexError/RecursionError, pypdf LimitReachedError) → catch-all in `extract()`; EML headers salvage raw values.
- HTML: `head`/`svg` no longer skip tags; `<body>` resets skipping.
- UTF-16 (BOM) text supported.
- Own too-large message; reasons, MIME types and notes single-lined with `clean_label`.
- Single-part message attachment → `part_id: root`; listing warns when the MIME walk hits its cap.
- Not done: raw `.eml` fixtures for MIME walking (PLAN §9). The Gmail API returns parsed JSON payloads, not raw MIME, so hand-built payload dicts test the real input shape.

## Carry-over notes
- Deploy (Phase 4): Dockerfile must `uv sync --extra cn`, and `WORKSPACE_MCP_TOOLS=gmail,drive,cn`.
- `get_drive_shareable_link` survives `--read-only`; added to the disable list (found by the stdio tools/list run).
- Upstream's PPTX extractor reads slides only, not speaker notes (relevant to PLAN §5.3, Phase 2).
- `tool_tiers.yaml` edit not needed: with `--tools` (no tier) all imported tools stay enabled (`core/tool_registry.py:166`).
- DI fallback (`mode="ocr"`, page rendering with pypdfium2) is Phase 2b.
