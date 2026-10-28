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
            assert {t.name for t in tools.tools} == {"environment_info"}
            result = await session.call_tool("environment_info", {})
            assert not result.is_error
            assert result.structured_content["status"] == "ok"
            assert result.structured_content["data"]["python_version"].startswith("3.14.")
            invalid = await session.call_tool("environment_info", {"extra": "fixture-secret"})
            assert invalid.is_error
            assert invalid.structured_content["error"]["code"] == "INVALID_ARGUMENT"
            assert "fixture-secret" not in invalid.model_dump_json()
