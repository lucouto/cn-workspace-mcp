"""cn-workspace-mcp fork: test-wide defaults (new file; see FORK_CHANGES.md).

The fork enforces the domain allowlist by default whenever OAuth 2.1 is on,
and refuses to start with an empty allowlist. Upstream's tests build OAuth
servers without one; they test other things, so they run the way an operator
would opt out explicitly. Allowlist tests set CN_ALLOWLIST_ENFORCE themselves.
"""

import pytest


@pytest.fixture(autouse=True)
def _allowlist_opt_out_for_upstream_tests(monkeypatch):
    monkeypatch.setenv("CN_ALLOWLIST_ENFORCE", "false")
