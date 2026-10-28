import importlib

import pytest
from pydantic import ValidationError

from devtools_mcp_server.models import InspectionError, StartupConfig


def module():
    assert importlib.util.find_spec("devtools_mcp_server.tools.http"), (
        "Bounded loopback health inspector missing"
    )
    return importlib.import_module("devtools_mcp_server.tools.http")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/health",
        "http://localhost.evil/health",
        "http://LOCALHOST/health",
        "http://127.1/health",
        "http://2130706433/health",
        "http://127.00.0.1/health",
        "http://[::ffff:127.0.0.1]/health",
        "http://[::1%25lo]/health",
        "http://user:fixture-secret@localhost/health",
        "http://localhost/health?",
        "http://localhost/health#",
        "http://localhost/%68ealth",
        "http://localhost/../health",
        "http://localhost/not-approved",
        "ftp://localhost/health",
        "http://localhost:0/health",
    ],
)
def test_remote_and_ambiguous_urls_rejected_before_connect(url):
    m = module()
    with pytest.raises(InspectionError):
        m.validate_health_url(url, StartupConfig())


@pytest.mark.parametrize(
    "url,address,hostname",
    [
        ("http://localhost/health", "127.0.0.1", "localhost"),
        ("http://127.0.0.2/status", "127.0.0.2", "127.0.0.2"),
        ("https://[::1]/ready", "::1", "::1"),
    ],
)
def test_pinned_loopback_target_preserves_tls_hostname(url, address, hostname):
    target = module().validate_health_url(url, StartupConfig())
    assert target.address == address
    assert target.hostname == hostname


def test_extra_health_paths_are_operator_only():
    m = module()
    target = m.validate_health_url(
        "http://localhost/api/health", StartupConfig(extra_health_paths=("/api/health",))
    )
    assert target.path == "/api/health"
    with pytest.raises(ValidationError):
        m.HttpCheckArgs(url="http://localhost/health", include_body=True)
    with pytest.raises(ValidationError):
        m.HttpCheckArgs(url="http://localhost/health", method="POST")
