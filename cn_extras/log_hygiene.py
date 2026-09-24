"""Keep OAuth secrets out of the uvicorn access log.

uvicorn logs every request's full path, query string included. Google's
redirect to ``/oauth2callback`` carries the one-time authorization code in
the query, so the access log would record it. The code is single-use and
exchanged within seconds, but it has no place in the logs. This filter drops
the query string of the OAuth paths that carry codes or tokens and leaves
every other line (``/mcp?account=2`` included) untouched.
"""

import logging

# Paths whose query string may carry an authorization code, state or token.
_REDACTED_PATHS = ("/oauth2callback", "/auth/callback")


class RedactOAuthQuery(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn.access args: (client_addr, method, full_path, http_version, status)
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path, sep, _ = args[2].partition("?")
            if sep and path in _REDACTED_PATHS:
                record.args = args[:2] + (path + "?<redacted>",) + args[3:]
        return True


_FILTER = RedactOAuthQuery()


def install() -> None:
    """Attach the filter to uvicorn's access logger (idempotent).

    Logger filters survive uvicorn's own dictConfig at startup, which only
    replaces handlers.
    """
    access = logging.getLogger("uvicorn.access")
    if _FILTER not in access.filters:
        access.addFilter(_FILTER)
