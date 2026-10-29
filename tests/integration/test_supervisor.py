import asyncio
import sys

import psutil
import pytest
from test_git_metadata import unborn_repository as unborn_repository  # noqa: PLC0414

from devtools_mcp_server import supervisor
from devtools_mcp_server.execution import resolve_executable
from devtools_mcp_server.models import RuntimeState, StartupConfig


def get_supervisor():
    cls = getattr(supervisor, "Supervisor", None)
    assert cls is not None, "isolated inspection supervisor is missing"
    instance = cls(StartupConfig())
    instance.runtime = RuntimeState(git_executable=resolve_executable("git"))
    return instance


@pytest.mark.anyio
async def test_git_runs_in_worker(unborn_repository):
    instance = get_supervisor()
    try:
        reply = await instance.call("git_branches", {"repository": str(unborn_repository)})
        assert reply.status == "ok"
        assert reply.data.branches == []
    finally:
        await instance.aclose()


@pytest.mark.anyio
async def test_fifth_call_is_busy_and_timed_out_slots_are_reusable(
    monkeypatch, tmp_path, unborn_repository
):
    instance = get_supervisor()
    original = supervisor.start_owned_worker
    script = tmp_path / "blocked.py"
    script.write_text("import sys,time; sys.stdin.buffer.read(); time.sleep(30)")

    async def blocked(command, env, cwd):
        return await original((sys.executable, "-I", str(script)), env, cwd)

    monkeypatch.setattr(supervisor, "start_owned_worker", blocked)
    monkeypatch.setattr(supervisor, "CALL_TIMEOUT", 1.0)
    calls = [
        asyncio.create_task(instance.call("git_branches", {"repository": str(unborn_repository)}))
        for _ in range(4)
    ]
    await asyncio.sleep(0)
    reply = await instance.call("git_branches", {"repository": str(unborn_repository)})
    assert reply.error.code == "BUSY"
    replies = await asyncio.gather(*calls)
    assert all(reply.error.code == "TIMEOUT" for reply in replies)
    monkeypatch.setattr(supervisor, "start_owned_worker", original)
    monkeypatch.setattr(supervisor, "CALL_TIMEOUT", 10.0)
    assert (
        await instance.call("git_branches", {"repository": str(unborn_repository)})
    ).status == "ok"
    await instance.aclose()


@pytest.mark.anyio
async def test_cancellation_cleans_owned_descendants_only(monkeypatch, tmp_path, unborn_repository):
    instance = get_supervisor()
    original = supervisor.start_owned_worker
    pid_file = tmp_path / "owned-pid"
    script = tmp_path / "owned.py"
    script.write_text(
        "import sys,time,subprocess,signal\\n".replace("\\n", "\n")
        + f"from pathlib import Path\nchild=subprocess.Popen([{sys.executable!r},'-c','import time;time.sleep(30)'])\n"
        + f"Path({str(pid_file)!r}).write_text(str(child.pid))\n"
        + "def stop(*args):\n child.terminate()\n child.wait()\n sys.exit(0)\n"
        + "signal.signal(signal.SIGTERM,stop)\nsys.stdin.buffer.read()\ntime.sleep(30)\n"
    )

    async def owned(command, env, cwd):
        return await original((sys.executable, "-I", str(script)), env, cwd)

    monkeypatch.setattr(supervisor, "start_owned_worker", owned)
    application = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)"
    )
    call = asyncio.create_task(
        instance.call("git_branches", {"repository": str(unborn_repository)})
    )
    try:
        async with asyncio.timeout(5):
            while not pid_file.exists():
                await asyncio.sleep(0.02)
        child_pid = int(pid_file.read_text())
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        async with asyncio.timeout(3):
            while psutil.pid_exists(child_pid):
                await asyncio.sleep(0.02)
        assert application.returncode is None
    finally:
        if application.returncode is None:
            application.terminate()
        await application.wait()
        await instance.aclose()


@pytest.mark.anyio
async def test_cancellation_during_launch_does_not_orphan_worker(
    monkeypatch, tmp_path, unborn_repository
):
    instance = get_supervisor()
    original = supervisor.start_owned_worker
    started = asyncio.Event()
    handles = []
    script = tmp_path / "launching.py"
    script.write_text("import sys; sys.stdin.buffer.read()")

    async def delayed(command, env, cwd):
        handle = await original((sys.executable, "-I", str(script)), env, cwd)
        handles.append(handle)
        started.set()
        await asyncio.sleep(0.1)
        return handle

    monkeypatch.setattr(supervisor, "start_owned_worker", delayed)
    call = asyncio.create_task(
        instance.call("git_branches", {"repository": str(unborn_repository)})
    )
    try:
        async with asyncio.timeout(5):
            await started.wait()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        assert handles[0].process.returncode is not None
    finally:
        for handle in handles:
            supervisor.stop_owned_worker(handle, force=True)
            await handle.process.wait()
        await instance.aclose()


@pytest.mark.anyio
async def test_delayed_reaping_does_not_block_timeout_or_free_capacity(
    monkeypatch, tmp_path, unborn_repository
):
    instance = get_supervisor()
    original = supervisor.start_owned_worker
    released = asyncio.Event()
    script = tmp_path / "slow-exit.py"
    script.write_text("import sys; sys.stdin.buffer.read()")

    async def slow_wait(command, env, cwd):
        handle = await original((sys.executable, "-I", str(script)), env, cwd)
        original_wait = handle.process.wait

        async def gated_wait():
            await released.wait()
            return await original_wait()

        handle.process.wait = gated_wait
        return handle

    monkeypatch.setattr(supervisor, "start_owned_worker", slow_wait)
    monkeypatch.setattr(supervisor, "CALL_TIMEOUT", 0.3)
    calls = [
        asyncio.create_task(instance.call("git_branches", {"repository": str(unborn_repository)}))
        for _ in range(4)
    ]
    try:
        done, pending = await asyncio.wait(calls, timeout=1.0)
        assert not pending, "tool deadlines must not wait indefinitely for exit acknowledgement"
        assert all(task.result().error.code == "TIMEOUT" for task in done)
        assert (
            await instance.call("git_branches", {"repository": str(unborn_repository)})
        ).error.code == "BUSY"
    finally:
        released.set()
        await asyncio.gather(*calls, return_exceptions=True)
        await instance.aclose()
