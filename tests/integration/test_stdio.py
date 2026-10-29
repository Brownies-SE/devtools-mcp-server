import json
import sys

import anyio
import pytest
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_git_metadata import commit
from test_git_metadata import unborn_repository as unborn_repository  # noqa: PLC0414


@pytest.mark.anyio
async def test_exactly_six_git_tools_over_stdio(unborn_repository, tmp_path):
    commit(unborn_repository)
    shadow = tmp_path / "unrelated working directory"
    shadow.mkdir()
    sentinel = tmp_path / "shadow-executed"
    fake = shadow / "devtools_mcp_server"
    fake.mkdir()
    (fake / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-I", "-m", "devtools_mcp_server"],
        cwd=str(shadow),
        env={"PYTHONPATH": str(shadow)},
    )
    with anyio.fail_after(40):
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "git_status",
                "git_diff",
                "git_log",
                "git_show",
                "git_branches",
                "git_changed_files",
            }
            for tool in tools.tools:
                assert tool.annotations.read_only_hint
                reply = await client.call_tool(tool.name, {"repository": str(unborn_repository)})
                assert not reply.is_error, reply.structured_content
                Draft202012Validator(tool.output_schema).validate(reply.structured_content)
                assert json.loads(reply.content[0].text) == reply.structured_content
                assert (
                    len(reply.model_dump_json(by_alias=True, exclude_unset=True).encode()) <= 65536
                )
                invalid = await client.call_tool(
                    tool.name, {"repository": str(unborn_repository), "extra": "fake-secret-123"}
                )
                assert invalid.is_error
                assert invalid.structured_content["error"]["code"] == "INVALID_ARGUMENT"
                assert "fake-secret-123" not in invalid.model_dump_json()
    assert not sentinel.exists()
