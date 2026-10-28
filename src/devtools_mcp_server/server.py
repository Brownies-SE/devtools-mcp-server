import logging
from contextlib import asynccontextmanager

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError

from .models import (
    Reply,
    StartupConfig,
)
from .results import SafeSendStream, to_mcp_result
from .supervisor import Supervisor
from .tools import definitions


def create_server(config: StartupConfig) -> Server:
    registry = {tool.name: tool for tool in definitions()}
    supervisor = Supervisor(config)

    @asynccontextmanager
    async def lifespan(server):
        await supervisor.bootstrap()
        try:
            yield {}
        finally:
            await supervisor.aclose()

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
        reply = await supervisor.call(params.name, params.arguments or {})
        return to_mcp_result(reply)

    server = Server(
        "devtools-mcp-server",
        version="0.1.0",
        lifespan=lifespan,
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
        await server.run(read, SafeSendStream(write), server.create_initialization_options())


def main() -> None:
    anyio.run(run_stdio, StartupConfig())
