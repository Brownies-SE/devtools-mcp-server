import importlib
import json
import os
import socket
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from devtools_mcp_server.execution import CommandCapture
from devtools_mcp_server.models import InspectionError, StartupConfig


def module():
    assert importlib.util.find_spec("devtools_mcp_server.tools.docker"), (
        "Local Docker adapter missing"
    )
    return importlib.import_module("devtools_mcp_server.tools.docker")


@pytest.mark.parametrize(
    "endpoint",
    [
        "tcp://127.0.0.1:2375",
        "ssh://localhost",
        "npipe:////remote/pipe/docker_engine",
        "unix://remote/socket",
        "http://localhost",
    ],
)
def test_only_local_socket_transports_are_permitted(endpoint):
    m = module()
    with pytest.raises(InspectionError):
        m.select_local_endpoint(StartupConfig(docker_endpoint=endpoint))


@pytest.mark.skipif(os.name == "nt", reason="Native Unix socket fixture")
def test_explicit_local_unix_socket_and_inherited_host_ignored(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setenv("DOCKER_HOST", "tcp://example.invalid:2375")
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "credential-config"))
    with tempfile.TemporaryDirectory(prefix="dt-", dir="/tmp") as directory:
        socket_path = Path(directory) / "docker.sock"
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(socket_path))
            endpoint = m.select_local_endpoint(
                StartupConfig(docker_endpoint="unix://" + str(socket_path))
            )
            assert endpoint == "unix://" + str(socket_path.resolve())


def test_selected_inspect_state_excludes_env_labels_and_health_log(inspection_context, monkeypatch):
    m = module()
    inspection_context.runtime = replace(
        inspection_context.runtime,
        docker_executable="/fixture/docker",
        docker_endpoint="unix:///fixture/docker.sock",
    )
    selected = {
        "id": "a" * 64,
        "name": "/fixture",
        "status": "running",
        "running": True,
        "health": "healthy",
        "restart_count": 0,
        "ports": {},
        "mount_destinations": ["/app"],
    }

    def command(program, arguments, ctx):
        assert program == "docker"
        assert arguments[:2] == ("container", "inspect")
        template = arguments[arguments.index("--format") + 1]
        assert (
            ".Env" not in template
            and ".Labels" not in template
            and ".Log" not in template
            and ".Config" not in template
        )
        return CommandCapture(0, json.dumps(selected).encode(), b"")

    monkeypatch.setattr(m, "run_command", command)
    reply = m.docker_inspect(m.DockerInspectArgs(container_id="a" * 12), inspection_context)
    assert reply.data.status == "running"
    assert reply.data.health == "healthy"
    assert set(reply.data.model_dump()) == set(selected)


def test_log_tail_redaction_handles_multiline_and_ambiguous_records(
    inspection_context, monkeypatch
):
    m = module()
    inspection_context.runtime = replace(
        inspection_context.runtime,
        docker_executable="/fixture/docker",
        docker_endpoint="unix:///fixture/docker.sock",
    )
    logs = b"YWJjZGVmZ2hpamtsbW5vcA==\n-----END PRIVATE KEY-----\nready\n-----BEGIN PRIVATE KEY-----\nYWJjZGVmZ2hpamtsbW5vcA==\n-----END PRIVATE KEY-----\npassword=fixture-secret\nunfinished"
    monkeypatch.setattr(m, "run_command", lambda *args: CommandCapture(0, logs, b""))
    reply = m.docker_logs(m.DockerLogsArgs(container_id="a" * 12), inspection_context)
    assert reply.redacted
    assert reply.status == "partial"
    assert "YWJjZGVm" not in reply.model_dump_json()
    assert "fixture-secret" not in reply.model_dump_json()
    assert "unfinished" not in reply.model_dump_json()
    assert "ready" in reply.data.text


def test_log_options_and_ids_are_strict():
    m = module()
    for values in [
        {"container_id": "--help"},
        {"container_id": "api"},
        {"container_id": "a" * 12, "tail": 1001},
        {"container_id": "a" * 12, "since": "2026-10-28T12:00:00"},
    ]:
        with pytest.raises(ValidationError):
            m.DockerLogsArgs(**values)


def test_startup_parser_keeps_policy_extensions_and_hides_invalid_values(tmp_path, capsys):
    from devtools_mcp_server import config

    parser = getattr(config, "parse_args", None)
    assert callable(parser), "Startup policy options missing"
    options = parser(["--exclude-secret-path", str(tmp_path), "--allow-health-path", "/api/health"])
    assert options.extra_secret_paths == (str(tmp_path),)
    assert options.extra_health_paths == ("/api/health",)
    with pytest.raises(SystemExit) as error:
        parser(["--unknown-fixture-secret-value"])
    assert error.value.code == 2
    assert "fixture-secret-value" not in capsys.readouterr().err


@pytest.mark.skipif(os.name == "nt", reason="Native Unix sockets")
def test_ambiguous_discovery_requires_explicit_endpoint(monkeypatch):
    m = module()
    with tempfile.TemporaryDirectory(prefix="dt-", dir="/tmp") as directory:
        first, second = [Path(directory) / name for name in ["one.sock", "two.sock"]]
        with socket.socket(socket.AF_UNIX) as one, socket.socket(socket.AF_UNIX) as two:
            one.bind(str(first))
            two.bind(str(second))
            monkeypatch.setattr(
                m, "_default_endpoints", lambda: ["unix://" + str(first), "unix://" + str(second)]
            )
            with pytest.raises(InspectionError) as error:
                m.select_local_endpoint(StartupConfig())
            assert error.value.code == "UNAVAILABLE"


def test_stderr_key_tail_is_sanitized_independently(inspection_context, monkeypatch):
    m = module()
    inspection_context.runtime = replace(
        inspection_context.runtime,
        docker_executable="/fixture/docker",
        docker_endpoint="unix:///fixture/docker.sock",
    )
    monkeypatch.setattr(
        m,
        "run_command",
        lambda *args: CommandCapture(0, b"ready\n", b"YWJj\n-----END PRIVATE KEY-----\n"),
    )
    reply = m.docker_logs(m.DockerLogsArgs(container_id="a" * 12), inspection_context)
    assert "YWJj" not in reply.data.text
    assert "ready" in reply.data.text
