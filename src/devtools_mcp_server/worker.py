import json
import logging
import sys
from pathlib import Path

from pydantic import ValidationError

from .execution import resolve_executable
from .models import (
    CaptureBudget,
    InspectionContext,
    InspectionError,
    Reply,
    RuntimeState,
    StartupConfig,
    ToolError,
)
from .results import to_mcp_result
from .tools import definitions
from .tools.docker import select_local_endpoint


def main() -> None:
    logging.getLogger().handlers.clear()
    logging.getLogger().addHandler(logging.NullHandler())
    raw = sys.stdin.buffer.readline(8 * 1048576 + 2)
    if len(raw) > 8 * 1048576 + 1 or not raw.endswith(b"\n"):
        return
    request = json.loads(raw)
    if request.get("operation") == "bootstrap":
        values = request["config"]
        config = StartupConfig(
            tuple(values["extra_secret_paths"]),
            tuple(values["extra_health_paths"]),
            values["docker_endpoint"],
        )
        endpoint, docker_error = None, None
        try:
            endpoint = select_local_endpoint(config)
        except InspectionError as error:
            docker_error = {"code": error.code, "message": error.message}
        except PermissionError:
            docker_error = {
                "code": "ACCESS_DENIED",
                "message": "Local Docker endpoint access was denied.",
            }
        except OSError:
            docker_error = {
                "code": "UNAVAILABLE",
                "message": "Local Docker endpoint cannot be verified.",
            }
        output = {
            "git_executable": resolve_executable(
                "git", excluded_directory=request.get("startup_directory")
            ),
            "docker_executable": resolve_executable(
                "docker", excluded_directory=request.get("startup_directory")
            ),
            "docker_endpoint": endpoint,
            "docker_error": docker_error,
        }
    else:
        registry = {tool.name: tool for tool in definitions()}
        tool = registry.get(request.get("name"))
        if tool is None:
            return
        config_values = request["config"]
        config = StartupConfig(
            tuple(config_values["extra_secret_paths"]),
            tuple(config_values["extra_health_paths"]),
            config_values["docker_endpoint"],
        )
        context = InspectionContext(
            config,
            request["deadline"],
            CaptureBudget(),
            RuntimeState(**request["runtime"]),
            Path(request["scratch_dir"]),
        )
        try:
            args = tool.input_model.model_validate(request["arguments"])
            reply = tool.handler(args, context)
        except ValidationError:
            reply = Reply[tool.data_model](
                status="error",
                error=ToolError(code="INVALID_ARGUMENT", message="Invalid inspection arguments."),
            )
        except InspectionError as error:
            reply = Reply[tool.data_model](
                status="error", error=ToolError(code=error.code, message=error.message)
            )
        except PermissionError:
            reply = Reply[tool.data_model](
                status="error",
                error=ToolError(code="ACCESS_DENIED", message="Operating system denied access."),
            )
        except FileNotFoundError:
            reply = Reply[tool.data_model](
                status="error",
                error=ToolError(
                    code="NOT_FOUND", message="Requested local resource was not found."
                ),
            )
        except Exception:  # noqa: BLE001 -- untrusted inspection failures must not expose details.
            reply = Reply[tool.data_model](
                status="error",
                error=ToolError(
                    code="UNAVAILABLE", message="Inspection could not collect evidence."
                ),
            )
        output = to_mcp_result(reply).structured_content
    encoded = json.dumps(output, ensure_ascii=False).encode() + b"\n"
    if len(encoded) <= 65536:
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    sys.stdin.buffer.read(1)


if __name__ == "__main__":
    main()
