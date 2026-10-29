import json
import sys
import tempfile

import anyio
import pytest
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError


@pytest.fixture
async def mcp_client(anyio_backend):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-I", "-m", "devtools_mcp_server"],
        env={"DEVELOPMENT_SECRET": "fixture-environment-secret"},
    )
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors, anyio.fail_after(60):
        async with (
            stdio_client(parameters, errlog=errors) as (read, write),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            yield client
        errors.seek(0)
        assert "fixture-environment-secret" not in errors.read()


@pytest.mark.anyio
async def test_every_tool_rejects_unknown_arguments_with_valid_error_schema(mcp_client):
    tools = await mcp_client.list_tools()
    assert len(tools.tools) == 15
    for tool in tools.tools:
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        reply = await mcp_client.call_tool(tool.name, {"extra": "fake-secret-123"})
        assert reply.is_error
        assert reply.structured_content["error"]["code"] == "INVALID_ARGUMENT"
        assert "fake-secret-123" not in reply.model_dump_json()
        Draft202012Validator(tool.output_schema).validate(reply.structured_content)


@pytest.mark.anyio
async def test_output_budget_covers_both_actual_wire_channels(mcp_client, tmp_path):
    source = tmp_path / "large.py"
    source.write_text(("visible λ " * 100 + "\n") * 800, encoding="utf-8", newline="\n")
    reply = await mcp_client.call_tool("files_read", {"path": str(source), "max_lines": 1000})
    assert not reply.is_error
    assert reply.structured_content["truncated"]
    assert reply.structured_content["status"] == "partial"
    assert len(reply.model_dump_json(by_alias=True, exclude_unset=True).encode()) <= 65536
    assert json.loads(reply.content[0].text) == reply.structured_content


@pytest.mark.anyio
async def test_unknown_tool_is_dispatch_error(mcp_client):
    with pytest.raises(MCPError) as error:
        await mcp_client.call_tool("unknown_fixture_tool", {})
    assert error.value.code == -32602
