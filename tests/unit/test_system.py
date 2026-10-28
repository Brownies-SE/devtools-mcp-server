import importlib
from types import SimpleNamespace

import psutil
import pytest

from devtools_mcp_server.models import EnvironmentArgs


@pytest.fixture
def fake_system_snapshot(monkeypatch):
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: 25.0)
    monkeypatch.setattr(
        psutil, "virtual_memory", lambda: SimpleNamespace(total=8192, available=4096, used=3000)
    )
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(total=2048, used=512))


def system():
    assert importlib.util.find_spec("devtools_mcp_server.tools.system"), "System inspector missing"
    return importlib.import_module("devtools_mcp_server.tools.system")


def test_system_metrics_have_named_units(fake_system_snapshot, inspection_context, tmp_path):
    module = system()
    reply = module.system_stats(module.SystemStatsArgs(disk_path=str(tmp_path)), inspection_context)
    assert reply.data.cpu_percent == 25.0
    assert reply.data.cpu_sample_seconds == 0.1
    assert reply.data.memory_total_bytes == 8192
    assert reply.data.memory_available_bytes == 4096
    assert reply.data.memory_used_bytes == 3000
    assert reply.data.disk_total_bytes > 0


def test_missing_dependencies_reported_without_environment_values(inspection_context):
    from devtools_mcp_server.tools.environment import inspect_environment

    reply = inspect_environment(EnvironmentArgs(), inspection_context)
    assert hasattr(reply.data, "capabilities"), "Capability reporting missing"
    assert reply.data.capabilities["git"].available is False
    assert reply.data.capabilities["docker"].available is False
    assert reply.data.limits["active_calls"] == 4
