import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_http_local import local_server, response


@asynccontextmanager
async def connected_client(*, command=None, cwd=None, env=None):
    parameters = StdioServerParameters(
        command=command or sys.executable,
        args=["-I", "-m", "devtools_mcp_server"],
        cwd=cwd,
        env=env,
    )
    with anyio.fail_after(30):
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as client:
            await client.initialize()
            yield client


@pytest.fixture
async def unhealthy_api_client(anyio_backend):
    async with connected_client() as client:
        with local_server(response(status=503)) as (url, _):
            yield client, url


@pytest.mark.anyio
async def test_unhealthy_api_status_is_observed_evidence(unhealthy_api_client):
    client, url = unhealthy_api_client
    reply = await client.call_tool("http_check", {"url": url})
    assert reply.is_error is False
    assert reply.structured_content["data"]["http_status"] == 503
    assert reply.structured_content["status"] == "ok"


@pytest.mark.anyio
async def test_no_listener_is_transport_failure():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    async with connected_client() as client:
        reply = await client.call_tool("http_check", {"url": f"http://127.0.0.1:{port}/health"})
    assert reply.is_error
    assert reply.structured_content["error"]["code"] == "UNAVAILABLE"
    assert reply.structured_content["data"] is None


@pytest.mark.anyio
async def test_isolated_launch_from_spaced_path_and_hostile_cwd(tmp_path):
    checkout = tmp_path / "checkout with spaces"
    checkout.mkdir()
    environment = checkout / ".venv"
    source_environment = Path(sys.executable).parent.parent
    if os.name == "nt":
        result = await anyio.run_process(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(environment), str(source_environment)],
            check=False,
        )
        assert result.returncode == 0, "Test virtual environment junction could not be created"
        executable = environment / "Scripts" / "python.exe"
    else:
        environment.symlink_to(source_environment, target_is_directory=True)
        executable = environment / "bin" / "python"
    hostile = tmp_path / "unrelated working directory"
    hostile.mkdir()
    sentinel = tmp_path / "shadow-executed"
    shadow = hostile / "devtools_mcp_server"
    shadow.mkdir()
    malicious = (
        f'from pathlib import Path\nPath({str(sentinel)!r}).touch()\nraise RuntimeError("shadow")\n'
    )
    (shadow / "__init__.py").write_text(malicious)
    (shadow / "__main__.py").write_text(malicious)
    (hostile / "sitecustomize.py").write_text(malicious)
    async with connected_client(
        command=str(executable), cwd=str(hostile), env={"PYTHONPATH": str(hostile)}
    ) as client:
        tools = await client.list_tools()
        assert len(tools.tools) == 15
        reply = await client.call_tool("environment_info", {})
        assert not reply.is_error
        assert reply.structured_content["data"]["python_version"].startswith("3.14.")
    assert not sentinel.exists()
