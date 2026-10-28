import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .models import InspectionContext, InspectionError

_GIT_COMMANDS = {
    "--version",
    "version",
    "rev-parse",
    "symbolic-ref",
    "for-each-ref",
    "log",
    "ls-files",
    "ls-tree",
    "cat-file",
    "diff-tree",
    "check-ignore",
    "hash-object",
    "check-attr",
}
_ENV_KEYS = ("PATH", "HOME", "USERPROFILE", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR")


def minimal_environment() -> dict[str, str]:
    return {key: value for key in _ENV_KEYS if (value := os.environ.get(key)) is not None}


def resolve_executable(name: str) -> str | None:
    suffixes = (".exe",) if os.name == "nt" else ("",)
    for directory in os.environ.get("PATH", os.defpath).split(os.pathsep):
        if not directory or not os.path.isabs(directory) or directory.startswith(("//", "\\\\")):
            continue
        for suffix in suffixes:
            candidate = Path(directory) / (name + suffix)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate.resolve())
    return None


@dataclass(frozen=True)
class CommandCapture:
    returncode: int
    stdout: bytes
    stderr: bytes


def run_command(
    program, arguments: tuple[str, ...], ctx: InspectionContext, *, capture="ordinary"
) -> CommandCapture:
    if program not in {"git", "docker"} or not arguments or capture not in {"ordinary", "git_blob"}:
        raise InspectionError("INVALID_ARGUMENT", "Unsupported inspection command.")
    if program == "git":
        remaining = list(arguments)
        private_view = False
        while remaining and remaining[0].startswith(("--git-dir=", "--work-tree=")):
            option = remaining.pop(0)
            value = option.split("=", 1)[1]
            if option.startswith("--git-dir="):
                if Path(value).resolve() != (ctx.scratch_dir / "git").resolve():
                    raise InspectionError(
                        "INVALID_ARGUMENT", "Git metadata must use the private view."
                    )
                private_view = True
            elif not Path(value).is_absolute():
                raise InspectionError(
                    "INVALID_ARGUMENT", "A verified absolute worktree is required."
                )
        if not remaining or remaining[0] not in _GIT_COMMANDS:
            raise InspectionError("INVALID_ARGUMENT", "Git operation is not permitted.")
        if remaining[0] not in {"--version", "version"} and not private_view:
            raise InspectionError(
                "INVALID_ARGUMENT", "Git inspection requires a private metadata view."
            )
        if any(
            arg in {"-w", "--write", "--delete", "--filters", "--textconv", "--follow-symlinks"}
            for arg in remaining[1:]
        ):
            raise InspectionError(
                "INVALID_ARGUMENT", "Git write or conversion options are forbidden."
            )
        if remaining[0] == "symbolic-ref" and [
            arg for arg in remaining[1:] if not arg.startswith("-")
        ] != ["HEAD"]:
            raise InspectionError("INVALID_ARGUMENT", "Only reading the current branch is allowed.")
        executable = ctx.runtime.git_executable
    else:
        if arguments[:2] not in {
            ("container", "ls"),
            ("container", "inspect"),
            ("container", "logs"),
        }:
            raise InspectionError("INVALID_ARGUMENT", "Docker operation is not permitted.")
        if any(arg in {"--follow", "-f", "--details"} for arg in arguments[2:]):
            raise InspectionError(
                "INVALID_ARGUMENT", "Unbounded or detailed Docker output is forbidden."
            )
        executable = ctx.runtime.docker_executable
    if executable is None:
        raise InspectionError("DEPENDENCY_MISSING", "The inspection executable is not installed.")
    env = minimal_environment()
    prefix = []
    if program == "git":
        env.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_LITERAL_PATHSPECS": "1",
            }
        )
        prefix = [
            "--no-pager",
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=" + str(ctx.scratch_dir / "empty-hooks"),
            "-c",
            "log.showSignature=false",
            "-c",
            "submodule.recurse=false",
        ]
    else:
        if ctx.runtime.docker_endpoint is None:
            raise InspectionError("UNAVAILABLE", "No supported local Docker endpoint is selected.")
        config = ctx.scratch_dir / "docker-config"
        config.mkdir(exist_ok=True)
        prefix = ["--config", str(config), "--host", ctx.runtime.docker_endpoint]
    process = subprocess.Popen(
        [executable, *prefix, *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=ctx.scratch_dir,
    )
    records = queue.Queue(maxsize=8)
    stop = threading.Event()

    def drain(pipe, label):
        try:
            while not stop.is_set():
                chunk = pipe.read(16384)
                while not stop.is_set():
                    try:
                        records.put((label, chunk), timeout=0.05)
                        break
                    except queue.Full:
                        continue
                if not chunk:
                    break
        except OSError:
            pass

    threads = [
        threading.Thread(target=drain, args=(pipe, label), daemon=True)
        for pipe, label in ((process.stdout, "stdout"), (process.stderr, "stderr"))
    ]
    for thread in threads:
        thread.start()
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    complete = set()
    source = f"command-{time.monotonic_ns()}"
    try:
        while len(complete) != 2:
            remaining = ctx.deadline - time.monotonic()
            if remaining <= 0:
                raise InspectionError("TIMEOUT", "Inspection command timed out.")
            try:
                label, chunk = records.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue
            if not chunk:
                complete.add(label)
                continue
            channel = "git_blob" if capture == "git_blob" and label == "stdout" else "ordinary"
            ctx.budget.consume(
                channel, len(chunk), source=source if channel == "git_blob" else None
            )
            outputs[label].extend(chunk)
        remaining = max(0.01, ctx.deadline - time.monotonic())
        returncode = process.wait(timeout=remaining)
        return CommandCapture(returncode, bytes(outputs["stdout"]), bytes(outputs["stderr"]))
    except subprocess.TimeoutExpired:
        raise InspectionError("TIMEOUT", "Inspection command timed out.") from None
    finally:
        stop.set()
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass
        for pipe in (process.stdout, process.stderr):
            pipe.close()
        for thread in threads:
            thread.join(timeout=0.1)
