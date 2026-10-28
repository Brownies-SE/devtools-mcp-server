import platform
import sys

from ..models import EnvironmentArgs, InspectionContext, Reply, StrictModel


class Capability(StrictModel):
    available: bool
    evidence: str


class EnvironmentData(StrictModel):
    operating_system: str
    python_version: str
    capabilities: dict[str, Capability]
    limits: dict[str, int]
    server_version: str


def inspect_environment(args: EnvironmentArgs, ctx: InspectionContext) -> Reply[EnvironmentData]:
    return Reply[EnvironmentData](
        data=EnvironmentData(
            operating_system=platform.system(),
            python_version=sys.version.split()[0],
            server_version="0.1.0",
            capabilities={
                "git": Capability(
                    available=ctx.runtime.git_executable is not None,
                    evidence="Executable available; repository access is checked per call."
                    if ctx.runtime.git_executable
                    else "Executable not found.",
                ),
                "docker": Capability(
                    available=ctx.runtime.docker_executable is not None
                    and ctx.runtime.docker_endpoint is not None,
                    evidence="Local endpoint selected; daemon readiness is checked per call."
                    if ctx.runtime.docker_endpoint
                    else "No verified local endpoint selected.",
                ),
                "files": Capability(
                    available=True, evidence="Native path policy applies to each source."
                ),
                "processes": Capability(
                    available=True, evidence="Visibility depends on OS permissions."
                ),
                "http": Capability(
                    available=True, evidence="Only allowed loopback health paths may be checked."
                ),
            },
            limits={
                "active_calls": 4,
                "call_seconds": 10,
                "http_seconds": 5,
                "result_bytes": 65536,
                "source_bytes": 1048576,
                "scan_bytes": 20971520,
                "candidate_entries": 10000,
                "page_items": 200,
                "excerpt_lines": 1000,
            },
        )
    )
