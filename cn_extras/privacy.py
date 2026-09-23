"""Public privacy page for the Google OAuth consent screen (docs/DEPLOY.md §1).

Served at /privacy by the connector itself, so its URL sits under the app's
authorised domain (cheminneuf.community). Only the files shipped in
cn_extras/static/privacy/ are served, by exact name: no paths, no traversal.
No third-party requests (fonts are self-hosted), hence a strict CSP.
"""

from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse

from core.server import server

STATIC_DIR = Path(__file__).parent / "static" / "privacy"

_MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".woff2": "font/woff2",
    ".png": "image/png",
    ".txt": "text/plain; charset=utf-8",
}

# Exact file names allowed, fixed at import time.
_FILES = {
    p.name: p
    for p in STATIC_DIR.iterdir()
    if p.is_file() and p.suffix in _MEDIA_TYPES and p.name != "index.html"
}

_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
        "font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _file(path: Path) -> FileResponse:
    # The page itself changes (versions of the policy): short cache. Fonts and
    # the logo don't: cache for a day.
    max_age = 300 if path.suffix == ".html" else 86400
    headers = {**_HEADERS, "Cache-Control": f"public, max-age={max_age}"}
    return FileResponse(path, media_type=_MEDIA_TYPES[path.suffix], headers=headers)


@server.custom_route("/privacy", methods=["GET", "HEAD"])
async def privacy_page(request: Request):
    return _file(STATIC_DIR / "index.html")


@server.custom_route("/privacy/{name}", methods=["GET", "HEAD"])
async def privacy_asset(request: Request):
    path = _FILES.get(request.path_params.get("name", ""))
    if path is None:
        return PlainTextResponse("Not found", status_code=404)
    return _file(path)
