import os
from pathlib import Path

import psutil
from pydantic import Field

from ..models import Reply, StrictModel, Warning
from ..paths import PathPolicy


class SystemStatsArgs(StrictModel):
    disk_path: str | None = Field(default=None, min_length=1, max_length=4096)


class SystemData(StrictModel):
    cpu_percent: float | None = None
    cpu_sample_seconds: float = 0.1
    memory_total_bytes: int | None = None
    memory_available_bytes: int | None = None
    memory_used_bytes: int | None = None
    swap_total_bytes: int | None = None
    swap_used_bytes: int | None = None
    disk_total_bytes: int | None = None
    disk_used_bytes: int | None = None
    disk_free_bytes: int | None = None
    disk_path: str


def system_stats(args: SystemStatsArgs, ctx) -> Reply[SystemData]:
    # The current volume, not the worker's scratch directory, is the default.
    requested = args.disk_path or str(Path.cwd().anchor)
    warnings = []
    with PathPolicy(ctx.config).check_storage(requested, "disk", ctx) as location:
        data = SystemData(disk_path=str(location.path))
        try:
            data.cpu_percent = min(100.0, max(0.0, float(psutil.cpu_percent(interval=0.1))))
            memory, swap = psutil.virtual_memory(), psutil.swap_memory()
            data.memory_total_bytes = memory.total
            data.memory_available_bytes = memory.available
            data.memory_used_bytes = memory.used
            data.swap_total_bytes, data.swap_used_bytes = swap.total, swap.used
        except OSError, psutil.Error:
            warnings.append(
                Warning(code="UNAVAILABLE", message="Some system metrics are unavailable.")
            )
        try:
            location.proof.verify()
            if os.name == "nt":
                # Use the verified volume's stable drive root; the final identity
                # check prevents reporting a different path after replacement.
                disk = psutil.disk_usage(str(location.path.anchor))
                data.disk_total_bytes, data.disk_used_bytes, data.disk_free_bytes = (
                    disk.total,
                    disk.used,
                    disk.free,
                )
            else:
                disk = os.fstatvfs(location.fd)
                data.disk_total_bytes = disk.f_blocks * disk.f_frsize
                data.disk_free_bytes = disk.f_bavail * disk.f_frsize
                data.disk_used_bytes = (disk.f_blocks - disk.f_bfree) * disk.f_frsize
            location.proof.verify()
        except OSError, psutil.Error:
            warnings.append(
                Warning(code="UNAVAILABLE", message="Verified filesystem usage is unavailable.")
            )
    return Reply[SystemData](data=data, status="partial" if warnings else "ok", warnings=warnings)
