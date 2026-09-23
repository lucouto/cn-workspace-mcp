"""Admin CLI: list who has a stored session, and purge one user's tokens.

Run inside the container (same env as the server):

    uv run python -m cn_extras.admin check      # can we decrypt the store?
    uv run python -m cn_extras.admin list
    uv run python -m cn_extras.admin purge someone@chemin-neuf.org [--dry-run] [--no-revoke]

Purging deletes the user's stored Google tokens and the FastMCP mappings that
point to them, and revokes the Google grant (unless --no-revoke). Their Claude
connector then has to sign in again, which the allowlist decides. Nothing else
is stored per user (stateless mode), so this is the whole of "delete my data".

Only the disk storage backend is supported (the production setup). The store
is opened exactly like core/server.py does: same directory, same Fernet key
derived from FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY (or the client secret).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import List, Optional

UPSTREAM = "mcp-upstream-tokens"
JTI = "mcp-jti-mappings"


def storage_directory() -> Path:
    configured = os.getenv("WORKSPACE_MCP_OAUTH_PROXY_DISK_DIRECTORY", "").strip()
    if configured:
        return Path(configured)
    home = os.getenv("FASTMCP_HOME", "").strip()
    return (
        Path(home) / "oauth-proxy"
        if home
        else Path("~/.fastmcp/oauth-proxy").expanduser()
    )


def _encrypted_store(directory: Path):
    """Mirror core/server.py's disk branch (keep in sync; see FORK_CHANGES.md)."""
    from cryptography.fernet import Fernet
    from fastmcp.server.auth.jwt_issuer import derive_jwt_key
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    from core.storage import make_sanitized_file_store

    override = os.getenv("FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY", "").strip()
    if override:
        jwt_key = derive_jwt_key(
            low_entropy_material=override, salt="fastmcp-jwt-signing-key"
        )
    else:
        secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
        if not secret:
            raise SystemExit(
                "Set FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY (or GOOGLE_OAUTH_CLIENT_SECRET) "
                "to the server's value."
            )
        jwt_key = derive_jwt_key(
            high_entropy_material=secret, salt="fastmcp-jwt-signing-key"
        )
    storage_key = derive_jwt_key(
        high_entropy_material=jwt_key.decode(), salt="fastmcp-storage-encryption-key"
    )
    return FernetEncryptionWrapper(
        key_value=make_sanitized_file_store(str(directory)),
        fernet=Fernet(key=storage_key),
    )


def _adapters(store):
    from fastmcp.server.auth.oauth_proxy.models import JTIMapping, UpstreamTokenSet
    from key_value.aio.adapters.pydantic import PydanticAdapter

    upstream = PydanticAdapter[UpstreamTokenSet](
        key_value=store, pydantic_model=UpstreamTokenSet, default_collection=UPSTREAM
    )
    mappings = PydanticAdapter[JTIMapping](
        key_value=store, pydantic_model=JTIMapping, default_collection=JTI
    )
    return upstream, mappings


def _keys(directory: Path, collection: str) -> List[str]:
    folder = directory / collection
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.json"))


async def _sessions(directory: Path):
    """[(upstream_token_id, email, token_set)], plus the count of unreadable entries."""
    from cn_extras.auth_provider import identity_from_idp_tokens

    upstream, _ = _adapters(_encrypted_store(directory))
    found, unreadable = [], 0
    for key in _keys(directory, UPSTREAM):
        try:
            token_set = await upstream.get(key=key)
        except Exception:
            token_set = None
        if token_set is None:
            unreadable += 1  # wrong key, expired, or sanitised filename
            continue
        email, _, _ = identity_from_idp_tokens(token_set.raw_token_data or {})
        found.append((key, (email or "").lower(), token_set))
    return found, unreadable


async def cmd_check(directory: Path) -> int:
    """Decrypt every stored entry: proves the key and directory match the server."""
    store = _encrypted_store(directory)
    bad_total = 0
    for folder in sorted(p for p in directory.iterdir() if p.is_dir()):
        ok = bad = 0
        for key in _keys(directory, folder.name):
            try:
                value = await store.get(key=key, collection=folder.name)
            except Exception:
                value = None
            ok, bad = (ok + 1, bad) if value is not None else (ok, bad + 1)
        bad_total += bad
        print(f"{folder.name}\treadable={ok}\tunreadable={bad}")
    if bad_total:
        print("-- some entries can't be read: wrong signing key, or expired entries.")
    return 1 if bad_total else 0


async def cmd_list(directory: Path) -> int:
    sessions, unreadable = await _sessions(directory)
    by_email = {}
    for _, email, token_set in sessions:
        by_email.setdefault(email or "(no e-mail)", []).append(token_set.created_at)
    for email in sorted(by_email):
        print(f"{email}\t{len(by_email[email])} session(s)")
    print(
        f"-- {len(sessions)} session(s), {len(by_email)} user(s), {unreadable} unreadable"
    )
    return 0


async def cmd_purge(directory: Path, email: str, dry_run: bool, revoke: bool) -> int:
    from cn_extras.auth_provider import revoke_google_grant

    target = email.strip().lower()
    store = _encrypted_store(directory)
    upstream, mappings = _adapters(store)
    sessions, _ = await _sessions(directory)
    doomed = {key: ts for key, mail, ts in sessions if mail == target}
    if not doomed:
        print(f"No stored session for {target}.")
        return 1

    jti_keys = []
    for key in _keys(directory, JTI):
        try:
            mapping = await mappings.get(key=key)
        except Exception:
            continue
        if mapping is not None and mapping.upstream_token_id in doomed:
            jti_keys.append(key)

    print(
        f"{target}: {len(doomed)} session(s), {len(jti_keys)} token mapping(s)"
        + (" [dry run, nothing changed]" if dry_run else "")
    )
    if dry_run:
        return 0
    for key, token_set in doomed.items():
        if revoke:
            await revoke_google_grant(
                {
                    "refresh_token": token_set.refresh_token,
                    "access_token": token_set.access_token,
                }
            )
        await upstream.delete(key=key)
    for key in jti_keys:
        await mappings.delete(key=key)
    print("Purged" + (" and revoked at Google." if revoke else " (grant not revoked)."))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cn_extras.admin")
    parser.add_argument(
        "--dir", type=Path, default=None, help="storage directory (default: from env)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="decrypt every entry (verifies key and directory)")
    sub.add_parser("list", help="list users with a stored session")
    purge = sub.add_parser("purge", help="delete (and revoke) one user's stored tokens")
    purge.add_argument("email")
    purge.add_argument("--dry-run", action="store_true")
    purge.add_argument("--no-revoke", action="store_true")
    args = parser.parse_args(argv)

    directory = args.dir or storage_directory()
    if not directory.is_dir():
        print(f"Storage directory not found: {directory}", file=sys.stderr)
        return 2
    if args.command == "check":
        return asyncio.run(cmd_check(directory))
    if args.command == "list":
        return asyncio.run(cmd_list(directory))
    return asyncio.run(
        cmd_purge(directory, args.email, args.dry_run, not args.no_revoke)
    )


if __name__ == "__main__":
    sys.exit(main())
