import logging

import uvicorn

from cn_extras import log_hygiene


def _access_line(path):
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0,
        '%s - "%s %s HTTP/%s" %d', ("10.0.0.1:1", "GET", path, "1.1", 200), None,
    )
    for f in logging.getLogger("uvicorn.access").filters:
        f.filter(record)
    return record.getMessage()


def test_callback_code_is_redacted():
    log_hygiene.install()
    line = _access_line("/oauth2callback?state=abc&code=4/0SECRET&scope=email")
    assert "SECRET" not in line and "state=abc" not in line
    assert "/oauth2callback?<redacted>" in line


def test_other_paths_untouched():
    log_hygiene.install()
    assert "/mcp?account=2" in _access_line("/mcp?account=2")
    assert "/oauth2callback" in _access_line("/oauth2callback")


def test_install_is_idempotent():
    log_hygiene.install()
    log_hygiene.install()
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, log_hygiene.RedactOAuthQuery) for f in access.filters) == 1


def test_filter_survives_uvicorn_logging_setup():
    log_hygiene.install()
    uvicorn.Config(app=lambda *a: None)  # runs uvicorn's dictConfig
    line = _access_line("/oauth2callback?code=4/0SECRET")
    assert "SECRET" not in line
