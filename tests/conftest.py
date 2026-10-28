import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def inspection_context(tmp_path):
    import time

    from devtools_mcp_server.models import (
        CaptureBudget,
        InspectionContext,
        RuntimeState,
        StartupConfig,
    )

    return InspectionContext(
        StartupConfig(), time.monotonic() + 10, CaptureBudget(), RuntimeState(), tmp_path
    )
