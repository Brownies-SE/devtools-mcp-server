import os
import re
import stat
import time
from datetime import datetime
from pathlib import Path

from pydantic import Field, field_validator

from ..execution import run_command
from ..models import (
    CaptureBudget,
    InspectionContext,
    InspectionError,
    PageArgs,
    Reply,
    RuntimeState,
    StartupConfig,
    StrictModel,
    Warning,
)
from ..pagination import page
from ..paths import PathPolicy
from ..redaction import RecordSanitizer


class DockerContainersArgs(PageArgs):
    include_stopped: bool = False


class DockerInspectArgs(StrictModel):
    container_id: str = Field(pattern=r"^[a-fA-F0-9]{1,64}$")


class DockerLogsArgs(DockerInspectArgs):
    since: str | None = Field(default=None, max_length=64)
    tail: int = Field(default=200, ge=1, le=1000)

    @field_validator("since")
    @classmethod
    def aware_timestamp(cls, value):
        if value is not None:
            timestamp = datetime.fromisoformat(value)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("A timestamp with timezone is required.")
            return timestamp.isoformat()
        return value


class DockerContainer(StrictModel):
    id: str
    name: str
    image: str
    state: str
    ports: str


class DockerContainersData(StrictModel):
    containers: list[DockerContainer]


class DockerInspectData(StrictModel):
    id: str
    name: str
    status: str
    running: bool
    health: str | None
    restart_count: int
    ports: dict[str, list[dict[str, str]] | None] | None
    mount_destinations: list[str]


class DockerLogsData(StrictModel):
    text: str


_LIST_FORMAT = '{"id":{{json .ID}},"name":{{json .Names}},"image":{{json .Image}},"state":{{json .State}},"ports":{{json .Ports}}}'
_INSPECT_FORMAT = '{"id":{{json .Id}},"name":{{json .Name}},"status":{{json .State.Status}},"running":{{json .State.Running}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},"restart_count":{{json .RestartCount}},"ports":{{json .NetworkSettings.Ports}},"mount_destinations":[{{range $index,$mount := .Mounts}}{{if $index}},{{end}}{{json $mount.Destination}}{{end}}]}'


def _default_endpoints():
    if os.name == "nt":
        return ["npipe:////./pipe/docker_engine", "npipe:////./pipe/dockerDesktopLinuxEngine"]
    paths = ["/var/run/docker.sock", "/run/docker.sock"]
    home = os.environ.get("HOME")
    if home:
        paths += [
            str(Path(home) / ".docker" / "run" / "docker.sock"),
            str(Path(home) / ".docker" / "desktop" / "docker.sock"),
        ]
    return ["unix://" + path for path in paths]


def _pipe_exists(endpoint):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel.WaitNamedPipeW.restype = wintypes.BOOL
    path = "\\\\.\\pipe\\" + endpoint.rsplit("/", 1)[1]
    return bool(kernel.WaitNamedPipeW(path, 0)) or ctypes.get_last_error() in {121, 231}


def _endpoint(value, config, *, explicit):
    if (
        len(value) > 4096
        or any(ord(char) < 32 for char in value)
        or any(char in value for char in "%?#")
    ):
        raise InspectionError("INVALID_ARGUMENT", "Invalid local Docker endpoint.")
    if re.fullmatch(r"npipe:////\./pipe/[A-Za-z0-9_.-]{1,256}", value):
        if os.name != "nt":
            raise InspectionError("UNSUPPORTED", "Named pipes require native Windows.")
        if not explicit and not _pipe_exists(value):
            raise InspectionError("NOT_FOUND", "Local Docker pipe is absent.")
        return value, value.casefold()
    if not value.startswith("unix:///") or os.name == "nt":
        raise InspectionError(
            "INVALID_ARGUMENT", "Only local Unix sockets or local named pipes are allowed."
        )
    path = value[7:]
    policy = PathPolicy(config)
    ctx = InspectionContext(
        config, time.monotonic() + 10, CaptureBudget(), RuntimeState(), Path.cwd()
    )
    try:
        resolved, _ = policy._canonical(path)
        with policy.check_storage(str(resolved.parent), "file", ctx) as parent:
            before = os.lstat(resolved)
            if not stat.S_ISSOCK(before.st_mode):
                raise InspectionError("UNSUPPORTED", "Docker endpoint is not a local Unix socket.")
            parent.proof.verify()
            after = os.lstat(resolved)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise InspectionError("ACCESS_DENIED", "Docker socket changed during verification.")
            return "unix://" + str(resolved), (after.st_dev, after.st_ino)
    except FileNotFoundError:
        raise InspectionError("NOT_FOUND", "Local Docker socket is absent.") from None


def select_local_endpoint(config: StartupConfig) -> str | None:
    if config.docker_endpoint is not None:
        return _endpoint(config.docker_endpoint, config, explicit=True)[0]
    endpoints = {}
    for value in _default_endpoints():
        try:
            selected, identity = _endpoint(value, config, explicit=False)
            endpoints[identity] = selected
        except InspectionError as error:
            if error.code not in {"NOT_FOUND", "UNSUPPORTED"}:
                raise
    if len(endpoints) > 1:
        raise InspectionError(
            "UNAVAILABLE", "Multiple local Docker endpoints exist; select one at launch."
        )
    return next(iter(endpoints.values()), None)


def _require(ctx):
    if ctx.runtime.docker_executable is None:
        raise InspectionError("DEPENDENCY_MISSING", "Docker CLI is not installed.")
    if ctx.runtime.docker_error is not None:
        error = ctx.runtime.docker_error
        if isinstance(error, dict):
            raise InspectionError(error["code"], error["message"])
        raise InspectionError(error.code, error.message)
    if ctx.runtime.docker_endpoint is None:
        raise InspectionError(
            "UNAVAILABLE", "No unambiguous local Docker endpoint was found at startup."
        )


def _command(arguments, ctx):
    _require(ctx)
    capture = run_command("docker", arguments, ctx)
    if capture.returncode:
        raise InspectionError(
            "UNAVAILABLE", "Local Docker inspection failed or the daemon is unavailable."
        )
    return capture


def docker_containers(args: DockerContainersArgs, ctx) -> Reply[DockerContainersData]:
    arguments = ("container", "ls", "--no-trunc", "--format", _LIST_FORMAT)
    if args.include_stopped:
        arguments += ("--all",)
    capture = _command(arguments, ctx)
    containers = [
        DockerContainer.model_validate_json(line) for line in capture.stdout.splitlines() if line
    ]
    containers.sort(key=lambda container: container.id)
    rows, cursor = page(
        containers, args, "docker_containers", {"include_stopped": args.include_stopped}
    )
    return Reply[DockerContainersData](
        data=DockerContainersData(containers=rows), next_cursor=cursor
    )


def docker_inspect(args: DockerInspectArgs, ctx) -> Reply[DockerInspectData]:
    capture = _command(
        ("container", "inspect", "--format", _INSPECT_FORMAT, args.container_id), ctx
    )
    data = DockerInspectData.model_validate_json(capture.stdout)
    return Reply[DockerInspectData](data=data)


def docker_logs(args: DockerLogsArgs, ctx) -> Reply[DockerLogsData]:
    arguments = ("container", "logs", "--tail", str(args.tail))
    if args.since is not None:
        arguments += ("--since", args.since)
    capture = _command((*arguments, args.container_id), ctx)
    sanitizer = RecordSanitizer(leading_fragment=True)
    text = sanitizer.feed(capture.stdout + capture.stderr) + sanitizer.finish()
    warnings = (
        [Warning(code="UNSUPPORTED", message="Incomplete or ambiguous log records were omitted.")]
        if sanitizer.ambiguous
        else []
    )
    return Reply[DockerLogsData](
        data=DockerLogsData(text=text.decode("utf-8")),
        redacted=sanitizer.redacted,
        status="partial" if warnings else "ok",
        warnings=warnings,
        omitted_count=sanitizer.omitted_count,
    )
