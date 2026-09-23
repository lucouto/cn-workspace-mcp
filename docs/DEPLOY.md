# Deploying cn-workspace-mcp (Phase 4)

Checklist from zero to the first working connection. **[You]** = needs your
accounts (Google Cloud, Coolify, DNS, Claude Team). **[Done]** = prepared in the repo.

Image: `Dockerfile.cn` (fixed production settings baked in). Per-deployment
values and secrets: `deploy/coolify.env.example`.

---

## 0. Decisions (settled 2026-09-23)

| Question | Decision |
|---|---|
| Single Workspace tenant? | **No → External**, In production. 100-user **lifetime** cap: keep the pilot small, don't let strangers try it. |
| Workspace domains | cheminneuf.community, wyd2027.org (you're admin), chemin-neuf.org (Workspace, **not** your admin). All on Workspace → `CN_ALLOWED_DOMAINS_WITHOUT_HD` stays empty. |
| gmail.com | **Specific addresses only**, listed in `CN_ALLOWED_EMAILS`. |
| Hostname | `gws.mcp.cheminneuf.community` (DNS record to the Coolify server). |
| Claude callback | `https://claude.ai/api/mcp/auth_callback` (hosted Claude), plus Claude Code loopback `http://localhost:*/callback`, `http://127.0.0.1:*/callback`. Source: claude.com/docs/connectors/building/authentication. Already in `deploy/coolify.env.example`, verified against FastMCP's matcher. |
| Privacy page | **`https://gws.mcp.cheminneuf.community/privacy`**, served by the connector itself (`cn_extras/static/privacy/`, CCN charte, FR/EN). It supplements the CCN general policy (linked), discloses Anthropic/Azure as recipients and the Google *Limited Use* statement. Contact: Secrétariat général – Protection des données. |

Facts from Claude's docs that matter here:
- On Team, **only an Owner (or Primary Owner)** can add the connector (*Organization settings → Connectors*). Members then click *Connect* individually.
- Claude reaches the server from **Anthropic's published IP ranges**. The server must be public (or those ranges allowlisted in any firewall/WAF). The same applies to `/.well-known/` discovery.
- Leave "Advanced settings → OAuth Client ID/Secret" **empty** in Claude: this server uses Dynamic Client Registration / CIMD, and Google's client ID stays server-side.

## 1. Google Cloud project [You]

Google Cloud console → **Google Auth Platform**:

1. Create the project `cn-workspace-mcp` (under the organisation if you have one).
2. **APIs & Services → Library**: enable **Gmail API** and **Google Drive API**.
3. **Branding**:
   - app name "Chemin Neuf – Claude connector";
   - support email; **no logo** (a logo requires Google's brand verification, which this unverified app avoids);
   - privacy policy URL: `https://gws.mcp.cheminneuf.community/privacy` (live as soon as the app is deployed);
   - authorised domain `cheminneuf.community`.
4. **Audience**: **External**, then click **Publish app** → *In production* (never leave it in Testing: 7-day tokens).
5. **Data access** → add exactly these 5 scopes:
   `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`,
   `.../auth/gmail.readonly`, `.../auth/drive.readonly`.
6. **Clients** → *Create client* → type **Web application**.
   Authorised redirect URI: `https://gws.mcp.cheminneuf.community/oauth2callback`.
   Copy the client ID and secret.

## 2. Secrets [You]

```bash
openssl rand -base64 48   # → FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY
```

Keep it in Coolify only. **Rotating it signs everyone out** (it also derives the session-store encryption key).

## 3. Coolify application [You]

1. **New resource** → Public/Private repository → `lucouto/cn-workspace-mcp`.
   Branch: `cn/main` (the fork's integration branch, already on GitHub).
2. **Build pack**: Dockerfile. **Dockerfile location**: `/Dockerfile.cn`.
3. **Ports exposed**: `8000`. **Domain**: `https://gws.mcp.cheminneuf.community` (Traefik issues the certificate).
4. **Persistent storage**: a volume mounted at **`/data`**. Without it, every redeploy signs everyone out.
5. **Environment variables**: copy `deploy/coolify.env.example`, fill the values, and mark the `SECRET` lines as secret.
6. Health check: the image has one (`/health`); leave Coolify's default or point it at `/health`.
7. Optional hardening: read-only root filesystem plus a tmpfs at `/tmp` (tested: works).
8. **Deploy.**

## 4. Smoke tests after deploy [You or me, no Google login needed]

```bash
H=https://gws.mcp.cheminneuf.community
curl -fsS $H/health                                           # 200
curl -s $H/.well-known/oauth-authorization-server | jq .scopes_supported   # exactly the 5 scopes
curl -s -o /dev/null -w '%{http_code}\n' -X POST $H/mcp -d '{}'            # 401
curl -s -X POST $H/register -H 'content-type: application/json' \
  -d '{"client_name":"x","redirect_uris":["https://evil.example/cb"],"token_endpoint_auth_method":"none"}'
# → rejected (not a Claude callback)
```

In the Coolify logs, check for:
`[allowlist] Active: 3 domain(s)… enforce=True` and `FileTreeStore … directory=/data/oauth-proxy`.

## 5. First connection [You]

1. A Team **Owner**: Claude → **Organization settings → Connectors → Add custom connector**. URL: `https://gws.mcp.cheminneuf.community/mcp`. Leave Advanced settings empty. Members then find it under their connectors.
2. **Connect** → Google sign-in with your CCN account.
   - External app: "Google hasn't verified this app" → *Advanced* → *Go to …*.
   - Accept the 5 scopes.
3. In a chat, ask Claude to call **whoami**. You should see your address and the 5 scopes.

## 6. Acceptance tests (PLAN §9) [You, 30 min]

| # | Test | Expected |
|---|---|---|
| 1 | `whoami` | your address, 5 scopes |
| 2 | "List the attachments of <an e-mail with a PDF>" | part_ids, types, sizes |
| 3 | Read that PDF | text with page markers, `untrusted` marker |
| 4 | Read a Google Doc | Markdown |
| 5 | Read a Google Sheet with ≥ 2 tabs | every sheet as CSV under `=== Sheet N ===` |
| 6 | Read a Google Slides deck with speaker notes | slide text + `Speaker notes:` |
| 7 | A PDF in a shared drive | readable |
| 8 | Redeploy/restart the app in Coolify, then call `whoami` | still connected |
| 9 | Connect with a gmail.com account not in `CN_ALLOWED_EMAILS` | browser shows "Access not allowed" |
| 10 | Two people connected at the same time, each runs `whoami` + a search | each sees only their own data |
| 11 | `docker exec <container> /app/.venv/bin/python -m cn_extras.admin list` | lists the connected addresses |

Also collect for the Phase 5 pilot: whether Docs Markdown export and the XLSX export of formulas behave as expected. (Code falls back safely either way.)

## 7. Per-domain admin approval [You]

Users of a domain whose admin restricts third-party apps see `Error 400: admin_policy_enforced`.

| Domain | Who | Action |
|---|---|---|
| cheminneuf.community | you | Admin console → Security → API controls → App access control → Configure new app → search the client ID → **Specific** (gmail.readonly, drive.readonly) |
| wyd2027.org | you | same |
| chemin-neuf.org | its admin (to identify) | send `docs/admin-request-note.md` with the client ID |

Test with one account per domain **before** announcing to the Team.

## 8. Operations

- **Who is connected:** `docker exec <c> /app/.venv/bin/python -m cn_extras.admin list`
- **Remove someone:** remove them from `CN_ALLOWED_*` → cut off on their next request; their refresh is refused and purged. For immediate deletion of their stored tokens: `... cn_extras.admin purge someone@domain` (add `--dry-run` first).
- **Store sanity after changing secrets:** `... cn_extras.admin check`, which fails if the signing key no longer matches the stored sessions.
- **Users disconnect themselves:** Claude connector settings, plus https://myaccount.google.com/permissions.
- **Rollback:** redeploy the previous commit in Coolify; sessions survive (volume).
- **Upstream updates:** rebase on a new upstream tag, then `uv lock`, run the tests, redeploy. Check the "Private FastMCP internals" list in FORK_CHANGES.md.
- **OCR later:** create the Azure resource, then set `CN_DI_ENABLED=true`, `CN_DI_ENDPOINT` and `CN_DI_KEY`.
