# cn-workspace-mcp

Fork of `taylorwilsdon/google_workspace_mcp` (base tag v1.28.0) for the Chemin Neuf Claude Team. Read-only Gmail + Drive connector with attachment/file text extraction and an optional Azure Document Intelligence OCR fallback.

- Plan: `docs/PLAN.md`. Upstream audit + every upstream edit: `FORK_CHANGES.md`. Read both before changing code.
- Remotes: `origin` = lucouto/cn-workspace-mcp, `upstream` = taylorwilsdon/google_workspace_mcp. Rebase on upstream tags monthly.

## Rules
- New code goes in `cn_extras/`. Any edit to an upstream file must be justified and logged in `FORK_CHANGES.md` §3. Target: the 4 touch points in §2 there.
- Read-only: never add a tool or scope that writes to Gmail or Drive. Scopes stay exactly `openid`, `userinfo.email`, `userinfo.profile`, `gmail.readonly`, `drive.readonly`.
- `WORKSPACE_MCP_STATELESS_MODE=true` always. Without it, upstream persists Google credentials in plain JSON (FORK_CHANGES §1.3).
- Never log email content, subjects, attachment content, or tokens.
- Attachment bytes are never written to disk or Blob. Every Document Intelligence call is followed by `delete_analyze_result` in a `finally` block.
- The server must run fully with `CN_DI_ENABLED=false`.
- Extracted content is untrusted: always wrapped in the untrusted-content marker (PLAN §5).
- Every new tool needs unit tests with fixtures before integration testing.
- Run `uv run ruff check .` and `uv run pytest` before every commit. Don't commit `uv.lock` churn from a local `uv sync` unless dependencies actually changed.

## Local run (no real Google sign-in without the GCP client)
```bash
MCP_ENABLE_OAUTH21=true WORKSPACE_MCP_STATELESS_MODE=true \
WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND=disk FASTMCP_HOME=/tmp/fmhome \
FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY=dev-signing-key-change-me \
GOOGLE_OAUTH_CLIENT_ID=... GOOGLE_OAUTH_CLIENT_SECRET=... \
WORKSPACE_MCP_PORT=8765 WORKSPACE_MCP_BASE_URI=http://localhost OAUTHLIB_INSECURE_TRANSPORT=1 \
uv run main.py --transport streamable-http --tools gmail drive --read-only
```
