"""Phase 3: domain allowlist, its three check points, and multi-user isolation."""

import logging
import time
from types import SimpleNamespace

import jwt
import pytest
from fastmcp.server.auth.oauth_proxy.models import ClientCode
from fastmcp.server.auth.oauth_proxy.proxy import OAuthProxy
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import TokenError
from starlette.responses import HTMLResponse, RedirectResponse

import cn_extras.auth_provider as ap
from cn_extras import allowlist
from cn_extras.allowlist import AllowlistConfigError

async_test = pytest.mark.asyncio


@pytest.fixture
def cn_domains(monkeypatch):
    monkeypatch.setenv(
        "CN_ALLOWED_DOMAINS", "cheminneuf.community, Chemin-Neuf.org,@wyd2027.org"
    )
    monkeypatch.setenv("CN_ALLOWED_EMAILS", "benevole@gmail.com")
    monkeypatch.setenv("CN_ALLOWLIST_ENFORCE", "true")


def _id_token(
    email="luciano@chemin-neuf.org", verified=True, hd="auto", **extra
) -> str:
    claims = {"iss": "https://accounts.google.com", "sub": "123", **extra}
    if hd == "auto" and email and "@" in email:
        hd = email.rsplit("@", 1)[1].lower()  # a Workspace account of its domain
    if hd and hd != "auto":
        claims["hd"] = hd
    if email is not None:
        claims["email"] = email
    if verified is not None:
        claims["email_verified"] = verified
    return jwt.encode(claims, "irrelevant-key-for-unsigned-read", algorithm="HS256")


class TestPolicy:
    @pytest.mark.parametrize(
        "email",
        [
            "luciano@chemin-neuf.org",
            "X@CHEMINNEUF.COMMUNITY",
            "jmj@wyd2027.org",
            "Benevole@gmail.com",
        ],
    )
    def test_allowed(self, cn_domains, email):
        hd = email.rsplit("@", 1)[1].lower()
        assert allowlist.load().check(email, True, hd)[0]

    @pytest.mark.parametrize(
        "email",
        [
            "someone@gmail.com",
            "x@sub.chemin-neuf.org",  # no subdomains
            "x@chemin-neuf.org.evil.com",
            "chemin-neuf.org@evil.com",
            "not-an-email",
        ],
    )
    def test_refused(self, cn_domains, email):
        allowed, reason = allowlist.load().check(email, True, "chemin-neuf.org")
        assert not allowed and "not allowed" in reason

    def test_unverified_or_missing_email_is_refused(self, cn_domains):
        assert not allowlist.load().check("luciano@chemin-neuf.org", False)[0]
        assert not allowlist.load().check("luciano@chemin-neuf.org", None)[0]
        assert not allowlist.load().check(None, True)[0]

    def test_inactive_without_configuration(self, monkeypatch):
        for var in ("CN_ALLOWED_DOMAINS", "CN_ALLOWED_EMAILS", "CN_ALLOWLIST_ENFORCE"):
            monkeypatch.delenv(var, raising=False)
        assert allowlist.load().check("anyone@gmail.com", False)[0]

    def test_enforce_with_empty_lists_refuses_everyone(self, monkeypatch):
        monkeypatch.setenv("CN_ALLOWLIST_ENFORCE", "true")
        monkeypatch.delenv("CN_ALLOWED_DOMAINS", raising=False)
        monkeypatch.delenv("CN_ALLOWED_EMAILS", raising=False)
        assert not allowlist.load().check("luciano@chemin-neuf.org", True)[0]


class TestStartup:
    def test_enforce_with_nothing_allowed_refuses_to_start(self, monkeypatch):
        monkeypatch.setenv("CN_ALLOWLIST_ENFORCE", "true")
        monkeypatch.delenv("CN_ALLOWED_DOMAINS", raising=False)
        monkeypatch.delenv("CN_ALLOWED_EMAILS", raising=False)
        with pytest.raises(AllowlistConfigError, match="Refusing to start"):
            allowlist.validate_at_startup()
        with pytest.raises(AllowlistConfigError):
            _provider()

    @pytest.mark.parametrize(
        "domains,emails", [("gmail", ""), ("a@b.org", ""), ("", "nobody")]
    )
    def test_malformed_entries_are_rejected(self, monkeypatch, domains, emails):
        monkeypatch.setenv("CN_ALLOWED_DOMAINS", domains)
        monkeypatch.setenv("CN_ALLOWED_EMAILS", emails)
        with pytest.raises(AllowlistConfigError, match="Invalid"):
            allowlist.validate_at_startup()

    def test_open_server_warns_loudly(self, monkeypatch, caplog):
        for var in ("CN_ALLOWED_DOMAINS", "CN_ALLOWED_EMAILS", "CN_ALLOWLIST_ENFORCE"):
            monkeypatch.delenv(var, raising=False)
        caplog.set_level(logging.WARNING)
        allowlist.validate_at_startup()
        assert "ANY Google account" in caplog.text

    def test_core_server_builds_the_allowlist_provider(self):
        import core.server as server_module

        assert server_module.GoogleProvider is ap.AllowlistGoogleProvider


class TestIdentityFromIdToken:
    def test_reads_email_verified_and_hd(self):
        assert ap.identity_from_idp_tokens({"id_token": _id_token()}) == (
            "luciano@chemin-neuf.org",
            True,
            "chemin-neuf.org",
        )

    def test_string_verified_flag(self):
        assert (
            ap.identity_from_idp_tokens({"id_token": _id_token(verified="true")})[1]
            is True
        )

    @pytest.mark.parametrize(
        "tokens", [None, {}, {"id_token": "not.a.jwt"}, {"access_token": "x"}]
    )
    def test_missing_or_garbage(self, tokens):
        assert ap.identity_from_idp_tokens(tokens) == (None, None, None)


# --- provider hooks, on a real provider with an in-memory store ---------------


def _provider():
    return ap.AllowlistGoogleProvider(
        client_id="cid.apps.googleusercontent.com",
        client_secret="secret",
        base_url="http://localhost:8000",
        jwt_signing_key="test-signing-key-123456",
        client_storage=MemoryStore(),
    )


async def _store_code(provider, id_token, code="CLIENT-CODE"):
    await provider._code_store.put(
        key=code,
        value=ClientCode(
            code=code,
            client_id="claude",
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            code_challenge="c",
            code_challenge_method="S256",
            scopes=["openid"],
            idp_tokens={
                "access_token": "ya29.x",
                "refresh_token": "1//r",
                "id_token": id_token,
            },
            expires_at=int(time.time()) + 300,
            created_at=time.time(),
        ),
        ttl=300,
    )
    return code


@pytest.fixture
def revoked(monkeypatch):
    calls = []

    async def fake_revoke(idp_tokens):
        calls.append(idp_tokens)

    monkeypatch.setattr(ap, "revoke_google_grant", fake_revoke)
    return calls


async def _drain_background():
    """Revocation runs in the background; let it finish before asserting."""
    import asyncio

    while ap._background:
        await asyncio.gather(*list(ap._background))


class TestCallbackHook:
    @pytest.fixture
    def upstream_redirects(self, monkeypatch):
        async def fake_callback(self, request):
            return RedirectResponse(
                "https://claude.ai/api/mcp/auth_callback?code=CLIENT-CODE&state=s", 302
            )

        monkeypatch.setattr(OAuthProxy, "_handle_idp_callback", fake_callback)

    @async_test
    async def test_allowed_user_is_redirected_to_claude(
        self, cn_domains, upstream_redirects, revoked
    ):
        provider = _provider()
        await _store_code(provider, _id_token("luciano@chemin-neuf.org"))
        response = await provider._handle_idp_callback(SimpleNamespace())
        assert isinstance(response, RedirectResponse)
        assert await provider._code_store.get(key="CLIENT-CODE") is not None
        assert revoked == []

    @async_test
    async def test_refused_user_gets_a_page_code_deleted_grant_revoked(
        self, cn_domains, upstream_redirects, revoked, caplog
    ):
        caplog.set_level(logging.WARNING)
        provider = _provider()
        await _store_code(provider, _id_token("stranger@gmail.com"))
        response = await provider._handle_idp_callback(SimpleNamespace())
        await _drain_background()
        assert isinstance(response, HTMLResponse) and response.status_code == 403
        assert b"stranger@gmail.com is not allowed" in response.body
        assert await provider._code_store.get(key="CLIENT-CODE") is None
        assert revoked and revoked[0]["refresh_token"] == "1//r"
        assert "domain=gmail.com" in caplog.text and "1//r" not in caplog.text

    @async_test
    async def test_upstream_error_pages_pass_through(self, cn_domains, monkeypatch):
        async def failing(self, request):
            return HTMLResponse("upstream error", status_code=400)

        monkeypatch.setattr(OAuthProxy, "_handle_idp_callback", failing)
        response = await _provider()._handle_idp_callback(SimpleNamespace())
        assert response.status_code == 400


class TestTokenExchangeHook:
    @pytest.fixture
    def upstream_exchange(self, monkeypatch):
        calls = []

        async def fake_exchange(self, client, authorization_code):
            calls.append(authorization_code.code)
            return "FASTMCP-TOKENS"

        monkeypatch.setattr(OAuthProxy, "exchange_authorization_code", fake_exchange)
        return calls

    @async_test
    async def test_allowed_user_gets_tokens(
        self, cn_domains, upstream_exchange, revoked
    ):
        provider = _provider()
        code = await _store_code(provider, _id_token("jmj@wyd2027.org"))
        out = await provider.exchange_authorization_code(
            None, SimpleNamespace(code=code)
        )
        assert out == "FASTMCP-TOKENS" and upstream_exchange == [code]

    @async_test
    async def test_refused_user_never_reaches_token_storage(
        self, cn_domains, upstream_exchange, revoked
    ):
        provider = _provider()
        code = await _store_code(
            provider, _id_token("x@chemin-neuf.org", verified=False)
        )
        with pytest.raises(TokenError) as info:
            await provider.exchange_authorization_code(None, SimpleNamespace(code=code))
        assert (
            info.value.error == "invalid_grant"
            and "not verified" in info.value.error_description
        )
        await _drain_background()
        assert upstream_exchange == []  # upstream never stored the Google tokens
        assert await provider._code_store.get(key=code) is None and revoked

    @async_test
    async def test_missing_id_token_is_refused(
        self, cn_domains, upstream_exchange, revoked
    ):
        provider = _provider()
        code = await _store_code(provider, None)
        with pytest.raises(TokenError):
            await provider.exchange_authorization_code(None, SimpleNamespace(code=code))

    @async_test
    async def test_unknown_code_is_left_to_upstream(
        self, cn_domains, upstream_exchange
    ):
        out = await _provider().exchange_authorization_code(
            None, SimpleNamespace(code="nope")
        )
        assert out == "FASTMCP-TOKENS"


class TestPerCallMiddleware:
    @pytest.fixture
    def token(self, monkeypatch):
        import core.server as server_module
        import fastmcp.server.dependencies as deps

        holder = {"token": None}
        monkeypatch.setattr(deps, "get_access_token", lambda: holder["token"])
        provider = _provider()
        monkeypatch.setattr(server_module, "get_auth_provider", lambda: provider)
        return holder

    async def _call(self):
        async def call_next(ctx):
            return "tool ran"

        return await ap.AllowlistMiddleware().on_request(SimpleNamespace(), call_next)

    @async_test
    async def test_allowed(self, cn_domains, token):
        token["token"] = SimpleNamespace(
            claims={"email": "a@chemin-neuf.org", "email_verified": True}
        )
        assert await self._call() == "tool ran"

    @async_test
    async def test_address_removed_from_allowlist_is_cut_off_immediately(
        self, cn_domains, token, monkeypatch
    ):
        from fastmcp.exceptions import ToolError

        token["token"] = SimpleNamespace(
            claims={"email": "benevole@gmail.com", "email_verified": True}
        )
        assert await self._call() == "tool ran"
        monkeypatch.setenv("CN_ALLOWED_EMAILS", "")
        with pytest.raises(ToolError, match="not allowed"):
            await self._call()

    @async_test
    async def test_verified_claim_forms(self, cn_domains, token):
        from fastmcp.exceptions import ToolError

        for claim in ("true", True, None):  # tokeninfo string, userinfo bool, absent
            token["token"] = SimpleNamespace(
                claims={"email": "a@chemin-neuf.org", "email_verified": claim}
            )
            assert await self._call() == "tool ran"
        token["token"] = SimpleNamespace(
            claims={"email": "a@chemin-neuf.org", "email_verified": "false"}
        )
        with pytest.raises(ToolError, match="not verified"):
            await self._call()

    @async_test
    async def test_no_token_is_refused_when_enforced(self, cn_domains, token):
        from fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="not signed in"):
            await self._call()

    @async_test
    async def test_no_token_passes_when_not_enforced(self, monkeypatch, token):
        monkeypatch.setenv("CN_ALLOWED_DOMAINS", "chemin-neuf.org")
        monkeypatch.setenv("CN_ALLOWLIST_ENFORCE", "false")
        assert await self._call() == "tool ran"

    def test_middleware_is_registered_inside_auth_info(self):
        from auth.auth_info_middleware import AuthInfoMiddleware
        from core.server import server

        _provider()
        kinds = [type(m) for m in server.middleware]
        assert kinds.count(ap.AllowlistMiddleware) == 1
        assert kinds.index(AuthInfoMiddleware) < kinds.index(ap.AllowlistMiddleware)


# --- multi-user isolation (PLAN §9) --------------------------------------------------


class TestUserIsolation:
    def test_requested_email_is_replaced_by_the_authenticated_one(self):
        from auth.service_decorator import _override_oauth21_user_email

        kwargs = {"user_google_email": "victim@chemin-neuf.org"}
        email, _ = _override_oauth21_user_email(
            True,
            "attacker@chemin-neuf.org",
            "victim@chemin-neuf.org",
            (),
            kwargs,
            ["service", "user_google_email"],
            "gmail_read_attachment",
        )
        assert email == "attacker@chemin-neuf.org"
        assert kwargs["user_google_email"] == "attacker@chemin-neuf.org"

    @async_test
    async def test_token_for_one_user_cannot_fetch_another_users_service(
        self, monkeypatch
    ):
        import auth.service_decorator as sd
        from auth.google_auth import GoogleAuthenticationError

        monkeypatch.setattr(sd, "get_auth_provider", lambda: object())
        monkeypatch.setattr(
            sd,
            "get_access_token",
            lambda: SimpleNamespace(claims={"email": "attacker@chemin-neuf.org"}),
        )
        with pytest.raises(GoogleAuthenticationError, match="does not match"):
            await sd.get_authenticated_google_service_oauth21(
                "gmail",
                "v1",
                "gmail_read_attachment",
                "victim@chemin-neuf.org",
                ["https://www.googleapis.com/auth/gmail.readonly"],
            )


# --- security review regressions ------------------------------------------------


class TestHostedDomain:
    def test_workspace_account_is_allowed(self, cn_domains):
        assert allowlist.load().check("a@chemin-neuf.org", True, "chemin-neuf.org")[0]

    def test_consumer_account_on_a_member_domain_is_refused(self, cn_domains):
        allowed, reason = allowlist.load().check("ghost@chemin-neuf.org", True, None)
        assert not allowed and "not a Google Workspace account" in reason

    def test_hd_of_another_domain_is_refused(self, cn_domains):
        assert not allowlist.load().check("a@chemin-neuf.org", True, "evil.com")[0]

    def test_domain_declared_without_workspace(self, cn_domains, monkeypatch):
        monkeypatch.setenv("CN_ALLOWED_DOMAINS_WITHOUT_HD", "wyd2027.org")
        assert allowlist.load().check("x@wyd2027.org", True, None)[0]
        assert not allowlist.load().check("x@chemin-neuf.org", True, None)[0]

    def test_individual_addresses_need_no_hd(self, cn_domains):
        assert allowlist.load().check("benevole@gmail.com", True, None)[0]

    def test_per_request_check_ignores_hd(self, cn_domains):
        # tokeninfo has no hd; it was enforced at sign-in.
        assert allowlist.load().check(
            "a@chemin-neuf.org", True, None, at_sign_in=False
        )[0]

    def test_no_hd_domain_must_be_allowed_too(self, monkeypatch):
        monkeypatch.setenv("CN_ALLOWED_DOMAINS", "chemin-neuf.org")
        monkeypatch.setenv("CN_ALLOWED_DOMAINS_WITHOUT_HD", "other.org")
        with pytest.raises(AllowlistConfigError, match="no-hd domain"):
            allowlist.validate_at_startup()

    @async_test
    async def test_consumer_ghost_is_refused_at_exchange(
        self, cn_domains, revoked, monkeypatch
    ):
        async def fake_exchange(self, client, code):
            return "TOKENS"

        monkeypatch.setattr(OAuthProxy, "exchange_authorization_code", fake_exchange)
        provider = _provider()
        code = await _store_code(provider, _id_token("ghost@chemin-neuf.org", hd=None))
        with pytest.raises(TokenError, match="Workspace"):
            await provider.exchange_authorization_code(None, SimpleNamespace(code=code))


class TestNormalisation:
    def test_trailing_dot_and_case_in_settings_and_email(self, monkeypatch):
        monkeypatch.setenv("CN_ALLOWED_DOMAINS", "CheminNeuf.Community.")
        monkeypatch.setenv("CN_ALLOWLIST_ENFORCE", "true")
        policy = allowlist.validate_at_startup()
        assert policy.domains == frozenset({"cheminneuf.community"})
        assert policy.check(
            "  Luciano@CheminNeuf.Community. ", True, "cheminneuf.community"
        )[0]


class TestFailClosedDefaults:
    def test_enforced_by_default_with_oauth21(self, monkeypatch):
        monkeypatch.delenv("CN_ALLOWLIST_ENFORCE", raising=False)
        monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")
        assert allowlist.load().enforce is True
        monkeypatch.setenv("CN_ALLOWLIST_ENFORCE", "false")
        assert allowlist.load().enforce is False

    def test_oauth21_server_without_allowlist_refuses_to_start(self, monkeypatch):
        monkeypatch.delenv("CN_ALLOWLIST_ENFORCE", raising=False)
        monkeypatch.delenv("CN_ALLOWED_DOMAINS", raising=False)
        monkeypatch.delenv("CN_ALLOWED_EMAILS", raising=False)
        monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")
        with pytest.raises(AllowlistConfigError, match="Refusing to start"):
            _provider()

    @async_test
    async def test_other_auth_modes_are_refused_when_enforced(
        self, cn_domains, monkeypatch
    ):
        import core.server as server_module
        from fastmcp.exceptions import ToolError

        monkeypatch.setattr(server_module, "get_auth_provider", lambda: object())

        async def call_next(ctx):
            return "tool ran"

        with pytest.raises(ToolError, match="authentication mode"):
            await ap.AllowlistMiddleware().on_request(SimpleNamespace(), call_next)

    @async_test
    async def test_refusal_through_the_real_server_pipeline(self, cn_domains):
        from fastmcp import Client

        import cn_extras.tools  # noqa: F401  (registers the middleware)
        from core.server import server

        async with Client(server) as client:  # handshake allowed
            with pytest.raises(Exception, match="Access refused"):
                await client.call_tool("whoami", {})
            with pytest.raises(Exception, match="Access refused"):
                await client.list_tools()


class TestRedirectHardening:
    @pytest.mark.parametrize(
        "uri",
        ["https://evil.example/cb#frag", "https://evil.example/cb?code=AAA"],
    )
    @async_test
    async def test_registration_rejects_fragment_or_code(self, cn_domains, uri):
        from mcp.server.auth.provider import RegistrationError
        from mcp.shared.auth import OAuthClientInformationFull

        info = OAuthClientInformationFull(
            client_id="c1", redirect_uris=[uri], token_endpoint_auth_method="none"
        )
        with pytest.raises(RegistrationError):
            await _provider().register_client(info)

    @async_test
    async def test_claude_callback_registers_fine(self, cn_domains):
        from mcp.shared.auth import OAuthClientInformationFull

        info = OAuthClientInformationFull(
            client_id="c2",
            redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
            token_endpoint_auth_method="none",
        )
        await _provider().register_client(info)

    @pytest.mark.parametrize(
        "location",
        [
            "https://evil.example/cb#frag?code=CLIENT-CODE&state=s",  # code hidden in fragment
            "https://evil.example/cb?code=UNKNOWN&state=s",  # code not ours
            "https://evil.example/cb?state=s",  # no code
        ],
    )
    @async_test
    async def test_unverifiable_redirect_fails_closed(
        self, cn_domains, monkeypatch, location
    ):
        async def fake_callback(self, request):
            return RedirectResponse(location, 302)

        monkeypatch.setattr(OAuthProxy, "_handle_idp_callback", fake_callback)
        provider = _provider()
        await _store_code(provider, _id_token("luciano@chemin-neuf.org"))
        response = await provider._handle_idp_callback(SimpleNamespace())
        assert isinstance(response, HTMLResponse) and response.status_code == 403

    @async_test
    async def test_injected_code_param_uses_the_real_last_code(
        self, cn_domains, monkeypatch, revoked
    ):
        async def fake_callback(self, request):
            return RedirectResponse(
                "https://x/cb?code=AAA&code=CLIENT-CODE&state=s", 302
            )

        monkeypatch.setattr(OAuthProxy, "_handle_idp_callback", fake_callback)
        provider = _provider()
        await _store_code(provider, _id_token("stranger@gmail.com"))
        response = await provider._handle_idp_callback(SimpleNamespace())
        await _drain_background()
        assert response.status_code == 403
        assert await provider._code_store.get(key="CLIENT-CODE") is None and revoked

    @async_test
    async def test_code_store_failure_fails_closed(self, cn_domains, monkeypatch):
        async def fake_callback(self, request):
            return RedirectResponse("https://x/cb?code=CLIENT-CODE", 302)

        monkeypatch.setattr(OAuthProxy, "_handle_idp_callback", fake_callback)
        provider = _provider()

        async def broken(**kwargs):
            raise RuntimeError("store down")

        monkeypatch.setattr(provider._code_store, "get", broken)
        with pytest.raises(RuntimeError):  # → HTTP 500; no redirect is sent
            await provider._handle_idp_callback(SimpleNamespace())


class TestRefreshAfterRemoval:
    async def _session(self, provider, id_token):
        from fastmcp.server.auth.oauth_proxy.models import JTIMapping, UpstreamTokenSet

        now = time.time()
        await provider._upstream_token_store.put(
            key="UP-1",
            value=UpstreamTokenSet(
                upstream_token_id="UP-1",
                access_token="ya29.x",
                refresh_token="1//google-refresh",
                refresh_token_expires_at=None,
                expires_at=now + 3600,
                token_type="Bearer",
                scope="openid",
                client_id="claude",
                created_at=now,
                raw_token_data={"id_token": id_token},
            ),
        )
        await provider._jti_mapping_store.put(
            key="JTI-1",
            value=JTIMapping(jti="JTI-1", upstream_token_id="UP-1", created_at=now),
        )
        # The JWT issuer is created lazily by get_routes(); stand in for it.
        provider._jwt_issuer = SimpleNamespace(
            verify_token=lambda token, **kw: {"jti": "JTI-1"}
        )

    @async_test
    async def test_removed_address_cannot_refresh_and_grant_is_purged(
        self, cn_domains, monkeypatch, revoked
    ):
        async def upstream_refresh(self, client, refresh_token, scopes):
            return "NEW-TOKENS"

        monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", upstream_refresh)
        provider = _provider()
        await self._session(provider, _id_token("benevole@gmail.com", hd=None))
        token = SimpleNamespace(token="fastmcp-refresh-jwt")
        assert await provider.exchange_refresh_token(None, token, []) == "NEW-TOKENS"

        monkeypatch.setenv("CN_ALLOWED_EMAILS", "")  # the volunteer is removed
        with pytest.raises(TokenError, match="not allowed"):
            await provider.exchange_refresh_token(None, token, [])
        await _drain_background()
        assert await provider._upstream_token_store.get(key="UP-1") is None
        assert await provider._jti_mapping_store.get(key="JTI-1") is None
        assert revoked[-1]["refresh_token"] == "1//google-refresh"


class TestRevocationScope:
    @async_test
    async def test_missing_id_token_is_refused_without_revoking(
        self, cn_domains, revoked, monkeypatch
    ):
        async def fake_exchange(self, client, code):
            return "TOKENS"

        monkeypatch.setattr(OAuthProxy, "exchange_authorization_code", fake_exchange)
        provider = _provider()
        code = await _store_code(provider, None)
        with pytest.raises(TokenError):
            await provider.exchange_authorization_code(None, SimpleNamespace(code=code))
        await _drain_background()
        assert revoked == []
