"""The public /privacy page: served, self-contained, no traversal."""

import pytest
from starlette.testclient import TestClient

import cn_extras.privacy  # noqa: F401  (registers the routes)
from core.server import server


@pytest.fixture(scope="module")
def client():
    return TestClient(server.http_app())


def test_page_is_public_and_hardened(client):
    r = client.get("/privacy")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "style-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "max-age=300" in r.headers["cache-control"]
    html = r.text
    assert (
        "Anthropic" in html
        and "Limited Use" in html
        and "Protection des données" in html
    )


@pytest.mark.parametrize("page", ["/privacy", "/guide", "/admin"])
def test_page_makes_no_third_party_requests(client, page):
    import re

    html = client.get(page).text
    refs = re.findall(r'(?:src|href)="([^"]+)"|url\("([^"]+)"\)', html)
    loaded = [
        a or b
        for a, b in refs
        if (a or b) and not (a or b).startswith(("#", "https://", "mailto:"))
    ]
    # Same-site only: every reference is a root-relative path on this server.
    assert loaded and all(u.startswith("/") and not u.startswith("//") for u in loaded)
    # every local asset the page references actually exists
    for url in set(loaded):
        assert client.get(url).status_code == 200, url


@pytest.mark.parametrize(
    "path",
    [
        "/privacy/index.html",
        "/privacy/..%2Fmain.py",
        "/privacy/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "/privacy/nope.woff2",
    ],
)
def test_only_listed_files_are_served(client, path):
    assert client.get(path).status_code == 404


def test_guide_page(client):
    r = client.get("/guide")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = r.text
    for needle in (
        "GWS mcp",
        "Customize",
        "Connectors",
        "Search and tools",
        "whoami",
        "admin_policy_enforced",
        "GWS mcp – 2nd account",
        "GWS mcp – 1st account",
        "Deux comptes Google",
        "Two Google accounts",
        'lang="en"',
    ):
        assert needle in html, needle
    assert "style=" not in html  # CSP forbids inline styles


def test_shared_stylesheet_is_served(client):
    r = client.get("/privacy/ccn-page.css")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")


def test_admin_guide_page(client):
    r = client.get("/admin")
    assert r.status_code == 200 and "noindex" in r.text
    for needle in (
        "Organization settings",
        "Custom",
        "Web",
        "https://gws.mcp.cheminneuf.community/mcp",
        "Advanced settings",
        "Enterprise-managed auth",
        "385359822646-ihp90c1i6llfha04tk1h4pa6vk5mjk25",
        "admin_policy_enforced",
        "100",
        "https://gws.mcp.cheminneuf.community/mcp?account=2",
        "GWS mcp – 2nd account",
        "GWS mcp – 1st account",
        'lang="en"',
    ):
        assert needle in r.text, needle
    assert "style=" not in r.text
