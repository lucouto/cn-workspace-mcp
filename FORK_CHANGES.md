# FORK_CHANGES.md

Fork of `taylorwilsdon/google_workspace_mcp`, based on tag **v1.28.0** (`8475cec`). FastMCP pinned by upstream: **3.4.7**.
Every change to an upstream file is listed in §3, with its justification. New code lives in `cn_extras/`.

---

## 1. Phase 0 findings (23 Sept 2026, verified in code and by a local run)

### 1.1 `get_gmail_attachment_content`, HTTP mode
- `gmail/gmail_tools.py:2223`. Its default path saves the attachment through `core/attachment_storage` and returns `/attachments/{id}` (1 h URL).
- With `WORKSPACE_MCP_STATELESS_MODE=true` (`gmail_tools.py:2380`) it skips disk and returns base64 only if `return_base64=true`. It does **no text extraction**.
- `get_gmail_message_content` has the same split (`gmail_tools.py:481`): inline when stateless, file + URL otherwise.
- Recursive MIME walker to reuse: `_extract_attachments(payload)` in `gmail/gmail_tools.py`.
- → Disable the tool and build `gmail_list_attachments` / `gmail_read_attachment` in `cn_extras/`.

### 1.2 Office / PDF extraction
- `get_drive_file_content` (`gdrive/drive_tools.py:344`):
  - exports Docs to `text/plain`, Sheets to `text/csv` (first sheet only), Slides to `text/plain`;
  - handles Office files with `extract_office_xml_text`, PDFs with `extract_pdf_text` (pypdf), images with `encode_image_content`.
- Download helpers: `_media_request`, `_download_file_bytes`, `download_media_bytes`, `ensure_within_file_size_limit` (`core/file_limits.py`).
- Limits: `WORKSPACE_MCP_MAX_FILE_BYTES` and `WORKSPACE_MCP_MAX_OFFICE_XML_BYTES` (`core/file_limits.py:26-27`, Office default 25 MiB).
- → Reusable as-is from `cn_extras/`. `_`-prefixed helpers are private: re-check them at each rebase.

### 1.3 Token storage and encryption
- OAuth 2.1 = FastMCP `GoogleProvider` (an `OAuthProxy`), built in `core/server.py:732`.
- Storage backend is chosen by `WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND` (`core/server.py:489-690`):
  - `disk` → `FileTreeStore` at `WORKSPACE_MCP_OAUTH_PROXY_DISK_DIRECTORY`, or `$FASTMCP_HOME/oauth-proxy`, or `~/.fastmcp/oauth-proxy`. Wrapped in `FernetEncryptionWrapper`.
  - `valkey` → `ValkeyStore`, also Fernet-wrapped.
  - `memory` → `MemoryStore`. **Loses everything on restart.**
  - unset → FastMCP's own default (`fastmcp/server/auth/oauth_proxy/proxy.py:518`): an encrypted `FileTreeStore` at `settings.home/oauth-proxy/<key-fingerprint>`. So the default is **encrypted disk, not memory**. That corrects PLAN.md's earlier claim.
- Encryption key: `derive_jwt_key(jwt_signing_key, salt="fastmcp-storage-encryption-key")`. The JWT key comes from `FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY`, or else from the client secret (`core/server.py:451-473`). Rotating either invalidates all sessions.
- Verified locally: after a DCR registration, the file on disk does not contain the client name in clear.
- **The legacy credential store is plaintext.** `LocalDirectoryCredentialStore` (`auth/credential_store.py:78`) writes plain JSON. In OAuth 2.1 mode *without* stateless mode, refreshed Google credentials are persisted there (`auth/google_auth.py:999-1010`, inside `get_credentials`). With `WORKSPACE_MCP_STATELESS_MODE=true` the store is never instantiated (`google_auth.py:848-851`, `fastmcp_server.py:138`). → **Stateless mode is mandatory for us.**
- Stateless mode does not touch OAuth proxy storage. `core/server.py` never checks it, and it only sets `stateless_http=True` on the transport (`main.py:972`).
- The in-memory `OAuth21SessionStore._sessions` (`auth/oauth21_session_store.py:237`) is rebuilt per request from the validated access token (`ensure_session_from_access_token`), so losing it on restart is harmless.

### 1.4 Domain allowlist
- `DWD_ALLOWED_DOMAINS` (`auth/oauth_config.py:250`) covers domain-wide delegation only. **Not reusable.**
- **Hook point:** subclass `GoogleProvider` and override `exchange_authorization_code(client, authorization_code)` (`proxy.py:1082`).
  - The Google callback (`_handle_idp_callback`, `proxy.py:2153`) fetches the Google tokens and keeps them only in the short-lived `_code_store`.
  - Long-term encrypted storage (`_upstream_token_store.put`, ~`proxy.py:1215`) happens inside `exchange_authorization_code`.
  - The override reads `self._code_store.get(key=code).idp_tokens["id_token"]`, decodes `email` / `email_verified`, and raises `TokenError("access_denied", …)` before `super()` when the email isn't allowed. A rejected user's refresh token is therefore **never stored**. Optionally revoke it at Google (`https://oauth2.googleapis.com/revoke`).
  - `id_token` is present because `openid` is in the scopes. It comes straight from Google's token endpoint over TLS, so signature verification is optional per OIDC Core §3.1.3.7.
  - Caveat: the user sees the failure at the MCP client's token step, not on Google's callback page. The error message must be clear.
- Upstream touch: one line in `core/server.py:732` to instantiate `cn_extras.auth.AllowlistGoogleProvider` instead of `GoogleProvider`.
- Defense in depth, no upstream edit: a FastMCP middleware in `cn_extras/` that re-checks the email on every tool call (the email comes from `AuthInfoMiddleware`, `auth/auth_info_middleware.py:100-115`).

### 1.5 Multi-user isolation
- In OAuth 2.1 mode, `user_google_email` is **overridden** with the authenticated identity (`auth/service_decorator.py:199-228`). An explicit mismatch raises (`service_decorator.py:419-422`). The isolation test in PLAN §9 should still be run with two real accounts.

### 1.6 Scopes and tool surface (verified by a local run)
- `--tools gmail drive --read-only` requests exactly `openid`, `userinfo.email`, `userinfo.profile`, `gmail.readonly`, `drive.readonly`. These are advertised in `/.well-known/oauth-authorization-server` and `/.well-known/oauth-protected-resource/mcp`.
- The server supports DCR (`/register`) and CIMD (`client_id_metadata_document_supported: true`). Unauthenticated `/mcp` returns 401 with `WWW-Authenticate: Bearer resource_metadata=…`.
- Env equivalents exist: `WORKSPACE_MCP_TOOLS`, `WORKSPACE_MCP_READ_ONLY`, `WORKSPACE_MCP_DISABLED_TOOLS`.
- Upstream Gmail/Drive tools to disable (PLAN §7 keeps only `search_gmail_messages` and `search_drive_files`):
  ```
  WORKSPACE_MCP_DISABLED_TOOLS=get_gmail_message_content,get_gmail_messages_content_batch,get_gmail_attachment_content,get_gmail_thread_content,get_gmail_threads_content_batch,list_gmail_labels,list_gmail_filters,get_drive_file_content,get_drive_file_download_url,list_drive_items,get_drive_file_permissions,check_drive_file_public_access,get_drive_shareable_link
  ```
  (Write tools are already dropped by `--read-only`. They're listed in `core/tool_tiers.yaml` if needed.)
- Hardening: set `WORKSPACE_MCP_ALLOWED_CLIENT_REDIRECT_URIS` to Claude's OAuth callback(s) so DCR can't register arbitrary redirect URIs (`core/server.py:724`). Confirm the exact Claude callback URL before deploy.

### 1.7 Local run log
- Config: `MCP_ENABLE_OAUTH21=true`, `WORKSPACE_MCP_STATELESS_MODE=true`, `WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND=disk`, `FASTMCP_HOME=<tmp>`, placeholder Google client ID/secret.
- Result: server starts, the disabled tools are confirmed in the log, and only `$FASTMCP_HOME/oauth-proxy/*` is written.
- **Restart test:** a client registered before the restart was still accepted afterwards (`/authorize` → 302 to consent), while an unknown client got 400. Storage survives restarts.
- **Not yet verified (needs the real GCP OAuth client, Phase 4):** a full Google sign-in, Google tokens surviving a restart, and the allowlist rejection path end to end.

---

## 2. Minimal upstream touch points for Phases 1–3

| # | File | Change | Why |
|---|---|---|---|
| 1 | `core/server.py` (~l.732) | `GoogleProvider(...)` → `AllowlistGoogleProvider(...)` from `cn_extras.auth` | Domain allowlist at token exchange (§1.4) |
| 2 | `main.py:214` `SERVICE_MODULES` | Add `"cn": "cn_extras.tools"` (one line). Run with `--tools gmail drive cn`. (`fastmcp_server.py:156` has a separate hard-coded import list, used only for FastMCP Cloud, so not our deploy path.) | New tools |
| 3 | ~~`core/tool_tiers.yaml`~~ | **Not needed** (Phase 1): with `--tools` and no `--tool-tier`, every imported tool stays enabled (`core/tool_registry.py:166`). Only needed if we ever run with `--tool-tier`. | — |
| 4 | `pyproject.toml` | Optional extra `cn` (Pillow, openpyxl; `pypdfium2`, `azure-ai-documentintelligence` in Phase 2b). `cn_extras` is picked up by `packages.find` automatically. | Dependencies |

Everything else (extraction, DI, heuristics, quota, middleware) stays inside `cn_extras/`. Upstream has no plugin/entry-point mechanism at v1.28.0. The `SERVICE_MODULES` map is the cleanest hook, and an upstream PR could make it env-extensible.

---

### Upstream code reused by `drive_read` (check at rebase)
`gdrive.drive_helpers.resolve_drive_item` (shortcuts, shared drives), `core.file_limits.download_media_bytes` (streaming cap), `core.file_limits.FileTooLargeError`, `core.utils.extract_office_xml_text` (DOCX only now). No upstream file was edited in Phase 2 beyond `pyproject.toml`/`uv.lock`.

## 3. Upstream file modifications (log)

| Date | File | Change | Reason |
|---|---|---|---|
| 2026-09-23 | `main.py` (`SERVICE_MODULES`) | +1 line: `"cn": "cn_extras.tools"` | Register the cn_extras tools as service `cn` (touch point #2) |
| 2026-09-23 | `pyproject.toml` | optional extra `cn = ["pillow>=11.0.0", "openpyxl>=3.1.0", "pypdfium2>=4.30.0", "azure-ai-documentintelligence>=1.0.0"]` (openpyxl: Phase 2, MIT; pypdfium2 Apache-2.0/BSD-3 and the Azure SDK MIT: Phase 2b) | Image resizing; sheet-aware XLSX reading. Deploy must install `--extra cn` (Dockerfile, Phase 4) |
| 2026-09-23 | `uv.lock` | Regenerated by `uv lock` | Pillow added. Most of the diff is lockfile-format churn from a newer uv (revision 1→3). At rebase: take upstream's lock, then re-run `uv lock` |
