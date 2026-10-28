import json
import logging
import time
from pathlib import Path

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from .models import (
    CaptureBudget,
    InspectionContext,
    InspectionError,
    Reply,
    RuntimeState,
    StartupConfig,
    ToolError,
)
from .tools import definitions


def create_server(config: StartupConfig) -> Server:
    registry = {tool.name: tool for tool in definitions()}

    async def list_tools(ctx, params):
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_model.model_json_schema(),
                    output_schema=Reply[tool.data_model].model_json_schema(),
                    annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False),
                )
                for tool in registry.values()
            ]
        )

    async def call_tool(ctx, params):
        tool = registry.get(params.name)
        if tool is None:
            raise MCPError(-32602, "Unknown tool")
        try:
            args = tool.input_model.model_validate(params.arguments or {})
            context = InspectionContext(
                config, time.monotonic() + 10, CaptureBudget(), RuntimeState(), Path.cwd()
            )
            reply = tool.handler(args, context)
        except ValidationError:
            reply = Reply[tool.data_model](
                status="error",
                error=ToolError(
                    code="INVALID_ARGUMENT", message="Arguments do not match the tool schema."
                ),
            )
        except InspectionError as error:
            reply = Reply[tool.data_model](
                status="error", error=ToolError(code=error.code, message=error.message)
            )
        data = reply.model_dump(mode="json")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))],
            structured_content=data,
            is_error=reply.status == "error",
        )

    server = Server(
        "devtools-mcp-server",
        version="0.1.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    server.middleware = []
    return server


async def run_stdio(config: StartupConfig) -> None:
    logging.getLogger().handlers.clear()
    logging.getLogger().addHandler(logging.NullHandler())
    server = create_server(config)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    anyio.run(run_stdio, StartupConfig())
