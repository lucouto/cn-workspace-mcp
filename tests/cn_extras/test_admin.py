"""Admin CLI: list and purge on a real encrypted file store."""

import asyncio
import time

import jwt
import pytest

from cn_extras import admin


def _id_token(email):
    return jwt.encode(
        {"email": email, "email_verified": True}, "x" * 32, algorithm="HS256"
    )


@pytest.fixture(autouse=True)
def _restore_event_loop():
    """asyncio.run() (fixture and admin.main) leaves the main thread with no
    event loop; later upstream tests expect one. Give them a fresh loop."""
    yield
    asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY", "admin-test-key-123456"
    )
    monkeypatch.setenv("WORKSPACE_MCP_OAUTH_PROXY_DISK_DIRECTORY", str(tmp_path))

    async def seed():
        from fastmcp.server.auth.oauth_proxy.models import JTIMapping, UpstreamTokenSet

        upstream, mappings = admin._adapters(admin._encrypted_store(tmp_path))
        now = time.time()
        for i, email in enumerate(
            ["a@chemin-neuf.org", "a@chemin-neuf.org", "b@wyd2027.org"]
        ):
            key = f"UP{i}"
            await upstream.put(
                key=key,
                value=UpstreamTokenSet(
                    upstream_token_id=key,
                    access_token=f"ya29.{i}",
                    refresh_token=f"1//r{i}",
                    refresh_token_expires_at=None,
                    expires_at=now + 3600,
                    token_type="Bearer",
                    scope="openid",
                    client_id="claude",
                    created_at=now,
                    raw_token_data={"id_token": _id_token(email)},
                ),
            )
            await mappings.put(
                key=f"JTI{i}",
                value=JTIMapping(jti=f"JTI{i}", upstream_token_id=key, created_at=now),
            )

    asyncio.run(seed())
    return tmp_path


def test_list(store_dir, capsys):
    assert admin.main(["list"]) == 0
    out = capsys.readouterr().out
    assert (
        "a@chemin-neuf.org\t2 session(s)" in out
        and "b@wyd2027.org\t1 session(s)" in out
    )
    assert "ya29" not in out and "1//r" not in out  # never print tokens


def test_check_passes_with_the_right_key(store_dir, capsys):
    assert admin.main(["check"]) == 0
    assert "mcp-upstream-tokens\treadable=3\tunreadable=0" in capsys.readouterr().out


def test_check_fails_with_the_wrong_key(store_dir, monkeypatch, capsys):
    monkeypatch.setenv(
        "FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY", "another-key-000000"
    )
    assert admin.main(["check"]) == 1


def test_dry_run_changes_nothing(store_dir, capsys):
    assert admin.main(["purge", "A@chemin-neuf.org", "--dry-run"]) == 0
    assert "2 session(s), 2 token mapping(s) [dry run" in capsys.readouterr().out
    assert len(list((store_dir / admin.UPSTREAM).glob("*.json"))) == 3


def test_purge_removes_only_that_user_and_revokes(store_dir, monkeypatch, capsys):
    import cn_extras.auth_provider as ap

    revoked = []

    async def fake_revoke(tokens):
        revoked.append(tokens["refresh_token"])

    monkeypatch.setattr(ap, "revoke_google_grant", fake_revoke)
    assert admin.main(["purge", "a@chemin-neuf.org"]) == 0
    assert sorted(revoked) == ["1//r0", "1//r1"]
    assert sorted(p.stem for p in (store_dir / admin.UPSTREAM).glob("*.json")) == [
        "UP2"
    ]
    assert sorted(p.stem for p in (store_dir / admin.JTI).glob("*.json")) == ["JTI2"]
    assert admin.main(["purge", "a@chemin-neuf.org"]) == 1  # nothing left


def test_no_revoke_flag(store_dir, monkeypatch):
    import cn_extras.auth_provider as ap

    called = []
    monkeypatch.setattr(ap, "revoke_google_grant", lambda t: called.append(t))
    assert admin.main(["purge", "b@wyd2027.org", "--no-revoke"]) == 0
    assert called == []


def test_missing_directory(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "WORKSPACE_MCP_OAUTH_PROXY_DISK_DIRECTORY", str(tmp_path / "nope")
    )
    assert admin.main(["list"]) == 2
