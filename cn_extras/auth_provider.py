"""GoogleProvider with the CN domain allowlist (docs/PLAN.md §6, FORK_CHANGES §1.4).

Check points, from friendliest to last line of defence:

1. Client registration (``register_client``): redirect URIs with a fragment or
   a ``code`` query parameter are refused (RFC 6749 §3.1.2 forbids fragments),
   so the callback can always find the code it is about to hand out.
2. Google callback (``_handle_idp_callback``): right after Google signs the user
   in, before redirecting back to Claude. A refused user gets a clear page in
   the browser; their short-lived code is deleted and their Google grant
   revoked. If the code can't be verified, the redirect is refused too.
3. Token exchange (``exchange_authorization_code``): the same check before any
   Google refresh token is stored.
4. Token refresh (``exchange_refresh_token``): an address removed from the
   allowlist can't renew; its stored Google tokens are deleted and revoked.
5. Every MCP request (``AllowlistMiddleware.on_request``): the e-mail on the
   access token is checked again, so a removal takes effect immediately. When
   the allowlist is enforced, requests without a token from this provider
   (other auth modes) are refused: the allowlist can't be bypassed by config.

``hd`` (Workspace hosted domain) is only in Google's id_token, so it is enforced
at 2-4 (sign-in and refresh), not per request.

The upstream touch point is one import line in core/server.py.
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Set, Tuple
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from fastmcp.server.auth.oauth_proxy.proxy import create_error_html
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.server.auth.provider import RegistrationError, TokenError
from starlette.responses import HTMLResponse, RedirectResponse

from cn_extras import allowlist

logger = logging.getLogger(__name__)

GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

Identity = Tuple[Optional[str], Optional[bool], Optional[str]]


def identity_from_idp_tokens(idp_tokens: Optional[Dict[str, Any]]) -> Identity:
    """(email, email_verified, hd) from Google's id_token.

    The id_token comes from this server's own back-channel call to Google's
    token endpoint (authenticated with the client secret, over TLS); a client
    can't inject one. Per OIDC Core §3.1.3.7 the signature need not be
    re-verified. Missing or undecodable → (None, None, None) → refused.
    """
    raw = (idp_tokens or {}).get("id_token")
    if not raw:
        return None, None, None
    try:
        claims = jwt.decode(raw, options={"verify_signature": False})
    except jwt.PyJWTError:
        return None, None, None
    verified = claims.get("email_verified")
    if isinstance(verified, str):  # some responses send "true"
        verified = verified.lower() == "true"
    return claims.get("email"), verified, claims.get("hd")


def _domain(email: Optional[str]) -> str:
    return email.rsplit("@", 1)[-1].lower() if email and "@" in email else "unknown"


async def revoke_google_grant(idp_tokens: Optional[Dict[str, Any]]) -> None:
    """Best effort: revoke a refused user's Google grant."""
    token = (idp_tokens or {}).get("refresh_token") or (idp_tokens or {}).get(
        "access_token"
    )
    if not token:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(GOOGLE_REVOKE_URL, data={"token": token})
    except httpx.HTTPError as exc:
        logger.warning(
            "[allowlist] Could not revoke refused grant: %s", type(exc).__name__
        )


_background: Set[asyncio.Task] = set()


def _revoke_in_background(idp_tokens: Optional[Dict[str, Any]]) -> None:
    # Don't make the refused user wait on Google's /revoke.
    task = asyncio.get_running_loop().create_task(revoke_google_grant(idp_tokens))
    _background.add(task)
    task.add_done_callback(_background.discard)


def _refusal_page(reason: str) -> HTMLResponse:
    return HTMLResponse(
        content=create_error_html(
            error_title="Access not allowed",
            error_message=f"Sign-in refused: {reason}",
        ),
        status_code=403,
    )


class AllowlistGoogleProvider(GoogleProvider):
    def __init__(self, *args, **kwargs):
        allowlist.validate_at_startup()
        super().__init__(*args, **kwargs)
        register_middleware()

    def _decide(self, identity: Identity) -> Tuple[bool, str]:
        email, verified, hd = identity
        return allowlist.load().check(email, verified, hd, at_sign_in=True)

    def _log_refusal(self, stage: str, email: Optional[str], reason: str) -> None:
        # The address is the user's own identity (allowed by the logging
        # policy); the domain helps filtering. Never tokens.
        logger.warning(
            "[allowlist] Refused at %s: email=%s domain=%s (%s)",
            stage,
            email or "unknown",
            _domain(email),
            reason,
        )

    async def _refuse_code(
        self, code: str, idp_tokens, identity: Identity, stage: str, reason: str
    ) -> None:
        await self._code_store.delete(key=code)
        # Revoke only for a known identity the policy rejects. A missing
        # id_token is more likely our misconfiguration than a stranger, and
        # revoking would kill the user's other live sessions.
        if identity[0]:
            _revoke_in_background(idp_tokens)
        self._log_refusal(stage, identity[0], reason)

    async def register_client(self, client_info) -> None:
        for uri in client_info.redirect_uris or []:
            text = str(uri)
            parts = urlsplit(text)
            if parts.fragment or "#" in text or "code" in parse_qs(parts.query):
                raise RegistrationError(
                    "invalid_redirect_uri",
                    "Redirect URIs must not contain a fragment or a 'code' parameter.",
                )
        await super().register_client(client_info)

    async def _handle_idp_callback(self, request):
        response = await super()._handle_idp_callback(request)
        if not isinstance(response, RedirectResponse):
            return response  # upstream error pages pass through
        location = response.headers.get("location", "")
        parts = urlsplit(location)
        codes = parse_qs(parts.query).get("code") or []
        # The proxy appends our code last; a registered URI can't carry one.
        code = codes[-1] if codes else None
        code_model = await self._code_store.get(key=code) if code else None
        if code_model is None or parts.fragment:
            # Fail closed: a redirect whose code we can't verify is never sent.
            for stray in codes:
                await self._code_store.delete(key=stray)
            self._log_refusal("callback", None, "unverifiable client redirect")
            return _refusal_page("the sign-in could not be verified. Please try again.")
        identity = identity_from_idp_tokens(code_model.idp_tokens)
        allowed, reason = self._decide(identity)
        if allowed:
            return response
        await self._refuse_code(
            code, code_model.idp_tokens, identity, "callback", reason
        )
        return _refusal_page(reason)

    async def exchange_authorization_code(self, client, authorization_code):
        code_model = await self._code_store.get(key=authorization_code.code)
        if code_model is not None:
            identity = identity_from_idp_tokens(code_model.idp_tokens)
            allowed, reason = self._decide(identity)
            if not allowed:
                await self._refuse_code(
                    authorization_code.code,
                    code_model.idp_tokens,
                    identity,
                    "token-exchange",
                    reason,
                )
                raise TokenError("invalid_grant", f"Sign-in refused: {reason}")
        # A missing code falls through: upstream raises its own invalid_grant.
        return await super().exchange_authorization_code(client, authorization_code)

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        # Same lookup as upstream (proxy.py exchange_refresh_token): refresh JWT
        # → JTI mapping → stored Google token set, whose raw_token_data still
        # holds the id_token of the original sign-in.
        try:
            payload = self.jwt_issuer.verify_token(
                refresh_token.token, expected_token_use="refresh"
            )
            mapping = await self._jti_mapping_store.get(key=payload["jti"])
            token_set = (
                await self._upstream_token_store.get(key=mapping.upstream_token_id)
                if mapping
                else None
            )
        except Exception:
            token_set, mapping, payload = None, None, None
        if token_set is not None:
            raw = token_set.raw_token_data or {}
            identity = identity_from_idp_tokens(raw)
            allowed, reason = self._decide(identity)
            if not allowed:
                await self._upstream_token_store.delete(key=mapping.upstream_token_id)
                await self._jti_mapping_store.delete(key=payload["jti"])
                if identity[0]:
                    _revoke_in_background(
                        {**raw, "refresh_token": token_set.refresh_token}
                    )
                self._log_refusal("refresh", identity[0], reason)
                raise TokenError("invalid_grant", f"Access refused: {reason}")
        # Anything we couldn't resolve is left to upstream, which rejects it.
        return await super().exchange_refresh_token(client, refresh_token, scopes)


# The handshake carries no data; refusing it would only show Claude a vague
# "Invalid request parameters" instead of our message on the first real call.
_UNGUARDED = {"initialize", "ping"}


class AllowlistMiddleware(Middleware):
    """Re-check the caller on every MCP request (tools, lists, resources, prompts)."""

    async def on_request(self, context: MiddlewareContext, call_next):
        from fastmcp.exceptions import ToolError
        from fastmcp.server.dependencies import get_access_token

        policy = allowlist.load()
        if not policy.active or getattr(context, "method", None) in _UNGUARDED:
            return await call_next(context)

        if policy.enforce:
            from core.server import get_auth_provider

            if not isinstance(get_auth_provider(), AllowlistGoogleProvider):
                # e.g. EXTERNAL_OAUTH21_PROVIDER, trusted gateway or legacy mode:
                # no sign-in path runs the allowlist, so nothing is served.
                logger.error(
                    "[allowlist] Enforced, but the active auth mode doesn't run it; refusing"
                )
                raise ToolError(
                    "Access refused: this server's authentication mode doesn't support "
                    "the allowlist. Contact the administrator."
                )

        try:
            token = get_access_token()
        except Exception:
            token = None
        if token is None:
            if policy.enforce:
                raise ToolError("Access refused: not signed in.")
            return await call_next(context)

        claims = getattr(token, "claims", None) or {}
        email = claims.get("email") or getattr(token, "email", None)
        verified = claims.get("email_verified")
        if isinstance(verified, str):
            verified = verified.lower() == "true"
        if verified is None:
            # Verification was enforced at sign-in; a missing claim here must
            # not lock everyone out. An explicit False is still refused.
            verified = True
        allowed, reason = policy.check(email, verified, at_sign_in=False)
        if not allowed:
            logger.warning(
                "[allowlist] Refused request: email=%s domain=%s",
                email or "unknown",
                _domain(email),
            )
            raise ToolError(f"Access refused: {reason}")
        return await call_next(context)


_registered = False


def register_middleware() -> None:
    """Add AllowlistMiddleware once. Added after upstream's AuthInfoMiddleware,
    so it runs inside it (FastMCP applies the first-added middleware outermost)."""
    global _registered
    if _registered:
        return
    from core.server import server

    server.add_middleware(AllowlistMiddleware())
    _registered = True
