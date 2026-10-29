import os
import socket
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from devtools_mcp_server.execution import minimal_environment, resolve_executable
from devtools_mcp_server.models import InspectionError, StartupConfig
from devtools_mcp_server.redaction import sanitize_text
from devtools_mcp_server.supervisor import Supervisor
from devtools_mcp_server.tools.docker import (
    _INSPECT_FORMAT,
    DockerContainersArgs,
    DockerInspectArgs,
    DockerLogsArgs,
    docker_containers,
    docker_inspect,
    docker_logs,
    select_local_endpoint,
)


@pytest.mark.anyio
@pytest.mark.skipif(os.name == "nt", reason="Native Unix socket fixture")
async def test_endpoint_is_frozen_after_bootstrap():
    with tempfile.TemporaryDirectory(prefix="dt-", dir="/tmp") as directory:
        first, second = Path(directory) / "one.sock", Path(directory) / "two.sock"
        with socket.socket(socket.AF_UNIX) as one, socket.socket(socket.AF_UNIX) as two:
            one.bind(str(first))
            instance = Supervisor(StartupConfig(docker_endpoint="unix://" + str(first)))
            try:
                await instance.bootstrap()
                endpoint = instance.runtime.docker_endpoint
                assert endpoint == "unix://" + str(first.resolve())
                first.unlink()
                two.bind(str(second))
                reply = await instance.call("environment_info", {})
                assert reply.status == "ok"
                assert instance.runtime.docker_endpoint == endpoint
            finally:
                await instance.aclose()


@pytest.mark.docker
def test_owned_container_state_and_logs(tmp_path, inspection_context, request):
    if "docker" not in request.config.option.markexpr:
        pytest.skip("Select -m docker explicitly to create the owned test container")
    executable = resolve_executable("docker")
    if executable is None:
        pytest.skip("Docker CLI is not installed")
    try:
        endpoint = select_local_endpoint(StartupConfig())
    except InspectionError:
        pytest.skip("An unambiguous local Docker endpoint is unavailable")
    if endpoint is None:
        pytest.skip("Local Docker daemon is unavailable")
    config = tmp_path / "test-docker-config"
    config.mkdir()
    prefix = [executable, "--config", str(config), "--host", endpoint]

    def command(*arguments):
        return subprocess.run(
            [*prefix, *arguments],
            env=minimal_environment(),
            capture_output=True,
            timeout=10,
            check=False,
        )

    image = "busybox:stable"
    if command("image", "inspect", "--format", "{{.Id}}", image).returncode:
        pytest.skip("Opt-in fixture requires busybox:stable already present locally")
    started = command(
        "run",
        "--detach",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--pids-limit",
        "16",
        "--memory",
        "32m",
        "--entrypoint",
        "/bin/sh",
        image,
        "-c",
        "printf 'ready\\npassword=fixture-secret\\n'; sleep 30",
    )
    assert started.returncode == 0, "Test container could not be started"
    container_id = started.stdout.decode().strip()
    inspection_context.runtime = replace(
        inspection_context.runtime, docker_executable=executable, docker_endpoint=endpoint
    )
    try:
        selected = command("container", "inspect", "--format", _INSPECT_FORMAT, container_id)
        assert selected.returncode == 0, sanitize_text(
            selected.stderr.decode(errors="replace")
        ).text
        reply = docker_inspect(DockerInspectArgs(container_id=container_id), inspection_context)
        assert reply.data.id == container_id
        rows = docker_containers(DockerContainersArgs(include_stopped=True), inspection_context)
        assert any(row.id == container_id for row in rows.data.containers)
        logs = docker_logs(DockerLogsArgs(container_id=container_id), inspection_context)
        assert "ready" in logs.data.text
        assert "fixture-secret" not in logs.model_dump_json()
        assert command("stop", "--time", "1", container_id).returncode == 0
        stopped = docker_inspect(DockerInspectArgs(container_id=container_id), inspection_context)
        assert stopped.data.running is False
        assert stopped.data.status == "exited"
    finally:
        assert command("rm", "--force", container_id).returncode == 0
