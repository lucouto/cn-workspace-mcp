"""Who may use this server: the domain / e-mail allowlist (docs/PLAN.md §6).

Policy only, no I/O, so it is easy to test and reuse at every check point:
the Google callback, the token exchange, token refresh and each request.

- CN_ALLOWED_DOMAINS: comma-separated domains (exact match, no subdomains).
- CN_ALLOWED_EMAILS: comma-separated individual addresses (e.g. a volunteer
  on gmail.com). These don't need a Workspace account.
- CN_ALLOWED_DOMAINS_WITHOUT_HD: allowed domains that are NOT Google Workspace
  domains (see below). Empty by default.
- CN_ALLOWLIST_ENFORCE: "true"/"false". Defaults to true whenever OAuth 2.1 is
  enabled (MCP_ENABLE_OAUTH21=true), so a deploy that forgets the lists can't
  open the server to every Google account: it refuses to start instead.

Domain matches require Google's ``hd`` (hosted domain) claim to equal the
domain at sign-in. Anyone can create a *consumer* Google account on any
address (``email_verified`` only proves they once clicked a link), so without
``hd`` a former member whose mailbox was deleted could keep signing in as
``x@chemin-neuf.org`` forever. ``hd`` is set only for accounts managed by that
domain's Workspace. Individual addresses in CN_ALLOWED_EMAILS skip this.
"""

import logging
import os
from dataclasses import dataclass
from typing import FrozenSet, Optional, Tuple

logger = logging.getLogger(__name__)


class AllowlistConfigError(ValueError):
    """The allowlist settings are unusable (e.g. enforce with nothing allowed)."""


def normalize_address(value: Optional[str]) -> str:
    """Lower-case, trim, and drop a trailing dot from the domain part."""
    return (value or "").strip().lower().rstrip(".")


@dataclass(frozen=True)
class Allowlist:
    domains: FrozenSet[str]
    emails: FrozenSet[str]
    domains_without_hd: FrozenSet[str]
    enforce: bool

    @property
    def active(self) -> bool:
        """True when requests are actually filtered."""
        return bool(self.domains or self.emails) or self.enforce

    def check(
        self,
        email: Optional[str],
        email_verified: Optional[bool],
        hd: Optional[str] = None,
        *,
        at_sign_in: bool = True,
    ) -> Tuple[bool, str]:
        """Return (allowed, reason); the reason is safe to show the user.

        ``at_sign_in`` enforces the ``hd`` rule. Per-request checks pass False:
        Google's tokeninfo carries no ``hd``, and it was enforced at sign-in.
        """
        if not self.active:
            return True, "no allowlist configured"
        if not email:
            return False, "Google did not return an e-mail address for this account."
        if email_verified is not True:
            return False, "the e-mail address of this Google account is not verified."
        address = normalize_address(email)
        if address in self.emails:
            return True, "e-mail allowed"
        domain = address.rsplit("@", 1)[-1] if "@" in address else ""
        if domain and domain in self.domains:
            if not at_sign_in or domain in self.domains_without_hd:
                return True, "domain allowed"
            if normalize_address(hd) == domain:
                return True, "domain allowed (Workspace account)"
            return False, (
                f"{address} is not a Google Workspace account of {domain} "
                "(it looks like a personal Google account created on that address). "
                "Sign in with your organisation account."
            )
        return False, (
            f"the account {address} is not allowed to use this connector. "
            "Sign in with your Chemin Neuf Google account, or ask the administrator "
            "to add your address."
        )


def _split(raw: str) -> FrozenSet[str]:
    return frozenset(
        normalize_address(item).lstrip("@") for item in raw.split(",") if item.strip()
    )


def _enforce_default() -> bool:
    raw = os.getenv("CN_ALLOWLIST_ENFORCE")
    if raw is not None and raw.strip():
        return raw.strip().lower() == "true"
    return os.getenv("MCP_ENABLE_OAUTH21", "false").strip().lower() == "true"


def load() -> Allowlist:
    """Read the allowlist from the environment (on every call: cheap, and
    lets tests and operators change it without restarting)."""
    return Allowlist(
        domains=_split(os.getenv("CN_ALLOWED_DOMAINS", "")),
        emails=_split(os.getenv("CN_ALLOWED_EMAILS", "")),
        domains_without_hd=_split(os.getenv("CN_ALLOWED_DOMAINS_WITHOUT_HD", "")),
        enforce=_enforce_default(),
    )


def validate_at_startup() -> Allowlist:
    """Fail fast on unusable settings; warn loudly when nothing is filtered."""
    allowlist = load()
    bad = [d for d in allowlist.domains if "@" in d or "." not in d]
    bad += [
        e for e in allowlist.emails if "@" not in e or "." not in e.rsplit("@", 1)[-1]
    ]
    bad += [d for d in allowlist.domains_without_hd if d not in allowlist.domains]
    if bad:
        raise AllowlistConfigError(
            "Invalid CN_ALLOWED_DOMAINS / CN_ALLOWED_EMAILS / CN_ALLOWED_DOMAINS_WITHOUT_HD "
            "entries (a no-hd domain must also be in CN_ALLOWED_DOMAINS): "
            + ", ".join(sorted(bad))
        )
    if allowlist.enforce and not (allowlist.domains or allowlist.emails):
        raise AllowlistConfigError(
            "The allowlist is enforced (CN_ALLOWLIST_ENFORCE, on by default with OAuth "
            "2.1) but CN_ALLOWED_DOMAINS and CN_ALLOWED_EMAILS are both empty: nobody "
            "could sign in. Refusing to start. Set them, or CN_ALLOWLIST_ENFORCE=false "
            "to run open on purpose."
        )
    if not allowlist.active:
        logger.warning(
            "[allowlist] Allowlist disabled: ANY Google account can use this server."
        )
    else:
        logger.info(
            "[allowlist] Active: %d domain(s) (%d without hd), %d individual address(es), enforce=%s",
            len(allowlist.domains),
            len(allowlist.domains_without_hd),
            len(allowlist.emails),
            allowlist.enforce,
        )
    return allowlist
