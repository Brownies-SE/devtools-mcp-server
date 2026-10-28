import platform
import sys

from ..models import EnvironmentArgs, InspectionContext, Reply, StrictModel


class EnvironmentData(StrictModel):
    operating_system: str
    python_version: str
    server_version: str


def inspect_environment(args: EnvironmentArgs, ctx: InspectionContext) -> Reply[EnvironmentData]:
    return Reply[EnvironmentData](
        data=EnvironmentData(
            operating_system=platform.system(),
            python_version=sys.version.split()[0],
            server_version="0.1.0",
        )
    )
