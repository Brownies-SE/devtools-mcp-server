import asyncio
import os
import signal
import subprocess
from dataclasses import dataclass


@dataclass
class WorkerHandle:
    process: asyncio.subprocess.Process
    job: object | None = None


async def start_owned_worker(command: tuple[str, ...], env: dict[str, str], cwd) -> WorkerHandle:
    options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cwd,
        limit=131072,
        **options,
    )
    handle = WorkerHandle(process)
    if os.name == "nt":
        from .windows import create_job

        try:
            handle.job = create_job(process.pid)
        except Exception:
            process.kill()
            await process.wait()
            raise
    return handle


async def send_request(handle: WorkerHandle, request: bytes) -> None:
    handle.process.stdin.write(request + b"\n")
    await handle.process.stdin.drain()


def stop_owned_worker(handle: WorkerHandle, force: bool = False) -> None:
    if os.name == "nt":
        if handle.job is not None:
            from .windows import close_job

            close_job(handle.job)
            handle.job = None
        elif handle.process.returncode is None:
            handle.process.kill()
    else:
        try:
            os.killpg(handle.process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass
