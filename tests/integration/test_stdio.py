import pytest

from devtools_mcp_server import server


@pytest.mark.anyio
async def test_stdio_environment_tool():
    assert callable(getattr(server, "create_server", None)), "MCP server is not implemented"
    import sys

    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(
        command=sys.executable, args=["-I", "-m", "devtools_mcp_server"]
    )
    with anyio.fail_after(30):
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {
                "environment_info",
                "files_list",
                "files_read",
                "files_search",
            }
            result = await session.call_tool("environment_info", {})
            assert not result.is_error
            assert result.structured_content["status"] == "ok"
            assert result.structured_content["data"]["python_version"].startswith("3.14.")
            invalid = await session.call_tool("environment_info", {"extra": "fixture-secret"})
            assert invalid.is_error
            assert invalid.structured_content["error"]["code"] == "INVALID_ARGUMENT"
            assert "fixture-secret" not in invalid.model_dump_json()


@pytest.mark.anyio
async def test_protocol_error_never_reflects_invalid_values():
    import asyncio
    import json
    import sys

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-m",
        "devtools_mcp_server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(15):
            initialize = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "fixture", "version": "0"},
                },
            }
            process.stdin.write((json.dumps(initialize) + "\n").encode())
            await process.stdin.drain()
            assert json.loads(await process.stdout.readline())["id"] == 1
            bad = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "environment_info", "arguments": ["fixture-secret-12345"]},
            }
            process.stdin.write((json.dumps(bad) + "\n").encode())
            await process.stdin.drain()
            response = await process.stdout.readline()
            parsed = json.loads(response)
            assert parsed["id"] == 2
            assert "error" in parsed
            assert b"fixture-secret-12345" not in response
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()
