# LOOP-STATE — Phase 1 (extraction + Gmail tools)

Spec: `docs/PLAN.md` §5.1, §5.2, §5.4 (whoami), shared extraction module, untrusted marker.
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
- `gmail_list_attachments` does not print Gmail `attachment_id`s (plan §5.1 lists them). They are ~600 chars and rotate between fetches; `part_id` is the stable key and `gmail_read_attachment` resolves the current ID itself. Keep this deviation?

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
