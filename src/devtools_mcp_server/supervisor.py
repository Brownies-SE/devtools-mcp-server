import asyncio
import json
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass

from pydantic import ValidationError

from .execution import minimal_environment
from .models import Reply, RuntimeState, StartupConfig, ToolError
from .platforms import send_request, start_owned_worker, stop_owned_worker
from .tools import definitions

CALL_TIMEOUT = 10.0


@dataclass(eq=False)
class _Lease:
    finished: bool = False
    reaped: bool = True


async def _read_reply(stream) -> dict:
    buffer = bytearray()
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            raise ValueError("Worker exited without a complete response.")
        buffer.extend(chunk)
        if len(buffer) > 65536:
            raise ValueError("Worker reply exceeds its bound.")
        if b"\n" in buffer:
            line, extra = bytes(buffer).split(b"\n", 1)
            if extra.strip():
                raise ValueError("Unexpected worker response.")
            return json.loads(line)


async def _discard_errors(stream) -> None:
    total = 0
    while chunk := await stream.read(4096):
        total += len(chunk)
        if total > 65536:
            raise ValueError("Worker error channel exceeds its bound.")


class Supervisor:
    def __init__(self, config: StartupConfig):
        self.config = config
        self.runtime = RuntimeState()
        self._leases = set()
        self._calls = set()
        self._handles = {}
        self._reapers = set()
        self._closed = False

    def _track_reaper(self, task):
        self._reapers.add(task)

        def done(completed):
            self._reapers.discard(completed)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(done)
        return task

    async def _reap(self, handle, scratch, lease):
        try:
            stop_owned_worker(handle)
            try:
                await asyncio.wait_for(handle.process.wait(), timeout=0.05)
            except TimeoutError:
                stop_owned_worker(handle, force=True)
                await handle.process.wait()
            stop_owned_worker(handle, force=True)
            await asyncio.to_thread(shutil.rmtree, scratch, True)
        finally:
            if handle.process.returncode is not None:
                self._handles.pop(handle.process.pid, None)
                if lease is not None:
                    lease.reaped = True
                    if lease.finished:
                        self._leases.discard(lease)

    async def _late_reap(self, launch, scratch, lease):
        try:
            handle = await launch
        except BaseException:  # noqa: BLE001 -- cancelled launches must release their resources.
            await asyncio.to_thread(shutil.rmtree, scratch, True)
            if lease is not None:
                lease.reaped = True
                if lease.finished:
                    self._leases.discard(lease)
            return
        self._handles[handle.process.pid] = (handle, scratch)
        await self._reap(handle, scratch, lease)

    async def _run(self, request: dict, deadline: float, lease=None) -> dict:
        scratch = tempfile.mkdtemp(prefix="devtools-inspect-")
        handle = None
        late = False
        tasks = []
        if lease is not None:
            lease.reaped = False
        launch = asyncio.create_task(
            start_owned_worker(
                (sys.executable, "-I", "-m", "devtools_mcp_server.worker"),
                minimal_environment(),
                scratch,
            )
        )
        try:
            try:
                handle = await asyncio.shield(launch)
            except asyncio.CancelledError:
                try:
                    handle = await asyncio.wait_for(
                        asyncio.shield(launch),
                        timeout=max(0.001, min(0.2, deadline - time.monotonic())),
                    )
                except TimeoutError, asyncio.CancelledError:
                    late = True
                    self._track_reaper(asyncio.create_task(self._late_reap(launch, scratch, lease)))
                raise
            self._handles[handle.process.pid] = (handle, scratch)
            if self._closed:
                raise ValueError("Supervisor is closed.")
            request.update(
                config=asdict(self.config),
                runtime=asdict(self.runtime),
                deadline=deadline,
                scratch_dir=scratch,
            )
            encoded = json.dumps(request, default=lambda obj: obj.model_dump()).encode()
            if len(encoded) > 8 * 1048576:
                raise ValueError("Worker input exceeds its bound.")
            await send_request(handle, encoded)
            response = asyncio.create_task(_read_reply(handle.process.stdout))
            errors = asyncio.create_task(_discard_errors(handle.process.stderr))
            tasks = [response, errors]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if response in done:
                return response.result()
            if errors.exception():
                raise errors.exception()
            return await response
        finally:
            if handle is not None:
                self._handles[handle.process.pid] = (handle, scratch)
                cleanup = self._track_reaper(
                    asyncio.create_task(self._reap(handle, scratch, lease))
                )
                try:
                    await asyncio.wait_for(
                        asyncio.shield(cleanup),
                        timeout=max(0.001, min(0.1, deadline - time.monotonic())),
                    )
                except TimeoutError, asyncio.CancelledError:
                    pass
            elif not late:
                shutil.rmtree(scratch, ignore_errors=True)
                if lease is not None:
                    lease.reaped = True
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def bootstrap(self) -> None:
        try:
            deadline = time.monotonic() + CALL_TIMEOUT
            async with asyncio.timeout_at(deadline - 0.1):
                state = await self._run({"operation": "bootstrap"}, deadline)
                self.runtime = RuntimeState(**state)
        except OSError, ValueError, TimeoutError:
            self.runtime = RuntimeState()

    async def call(self, name: str, arguments: dict) -> Reply:
        registry = {tool.name: tool for tool in definitions()}
        tool = registry.get(name)
        if tool is None:
            return Reply(
                status="error", error=ToolError(code="INVALID_ARGUMENT", message="Unknown tool.")
            )
        reply_type = Reply[tool.data_model]
        if self._closed or len(self._leases) >= 4:
            return reply_type(
                status="error", error=ToolError(code="BUSY", message="Inspection capacity is busy.")
            )
        lease = _Lease()
        self._leases.add(lease)
        task = asyncio.current_task()
        self._calls.add(task)
        deadline = time.monotonic() + CALL_TIMEOUT
        try:
            try:
                args = tool.input_model.model_validate(arguments)
            except ValidationError:
                return reply_type(
                    status="error",
                    error=ToolError(
                        code="INVALID_ARGUMENT", message="Arguments do not match the tool schema."
                    ),
                )
            async with asyncio.timeout_at(deadline - 0.1):
                raw = await self._run(
                    {"operation": "tool", "name": name, "arguments": args.model_dump(mode="json")},
                    deadline,
                    lease,
                )
                return reply_type.model_validate(raw)
        except TimeoutError:
            return reply_type(
                status="error", error=ToolError(code="TIMEOUT", message="Inspection timed out.")
            )
        except OSError, ValueError, ValidationError:
            return reply_type(
                status="error",
                error=ToolError(
                    code="UNAVAILABLE", message="Inspection worker could not return evidence."
                ),
            )
        finally:
            self._calls.discard(task)
            lease.finished = True
            if lease.reaped:
                self._leases.discard(lease)

    async def aclose(self) -> None:
        self._closed = True
        current = asyncio.current_task()
        calls = [task for task in self._calls if task is not current]
        for task in calls:
            task.cancel()
        for handle, _ in list(self._handles.values()):
            stop_owned_worker(handle, force=True)
        if calls:
            await asyncio.wait(calls, timeout=0.3)
        if self._reapers:
            await asyncio.wait(self._reapers, timeout=0.3)
