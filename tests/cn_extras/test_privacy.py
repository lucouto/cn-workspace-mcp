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
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert "max-age=300" in r.headers["cache-control"]
    html = r.text
    assert (
        "Anthropic" in html
        and "Limited Use" in html
        and "Protection des données" in html
    )


def test_page_makes_no_third_party_requests(client):
    import re

    html = client.get("/privacy").text
    refs = re.findall(r'(?:src|href)="([^"]+)"|url\("([^"]+)"\)', html)
    loaded = [
        a or b
        for a, b in refs
        if (a or b) and not (a or b).startswith(("#", "https://", "mailto:"))
    ]
    assert loaded and all(u.startswith("/privacy/") for u in loaded)
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
