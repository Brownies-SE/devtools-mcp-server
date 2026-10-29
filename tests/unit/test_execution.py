import subprocess
import sys

import pytest

from devtools_mcp_server import execution
from devtools_mcp_server.models import InspectionError, RuntimeState


@pytest.mark.parametrize(
    ("program", "args"),
    [
        ("sh", ("-c", "echo unsafe")),
        ("git", ("push",)),
        ("git", ("reset", "--hard")),
        ("git", ("hash-object", "-w", "--stdin")),
        ("git", ("symbolic-ref", "HEAD", "refs/heads/forbidden")),
        ("git", ("cat-file", "--filters", "HEAD:app.py")),
    ],
)
def test_execution_rejects_mutation_and_shell(program, args, inspection_context):
    function = getattr(execution, "run_command", None)
    assert callable(function), "controlled executable adapter is missing"
    with pytest.raises(InspectionError) as caught:
        function(
            program,
            (("--git-dir=" + str(inspection_context.scratch_dir / "git"),) + args)
            if program == "git"
            else args,
            inspection_context,
        )
    assert caught.value.code == "INVALID_ARGUMENT"


def test_subprocess_output_is_bounded_while_reading(monkeypatch, inspection_context):
    function = getattr(execution, "run_command", None)
    assert callable(function), "incremental capture is missing"
    original = subprocess.Popen

    def flood(command, **kwargs):
        return original(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 2097152)"], **kwargs
        )

    monkeypatch.setattr(execution.subprocess, "Popen", flood)
    inspection_context.runtime = RuntimeState(git_executable=sys.executable)
    with pytest.raises(InspectionError) as caught:
        function("git", ("--version",), inspection_context)
    assert caught.value.code == "OUTPUT_LIMIT"


def test_capture_capacity_is_charged_before_queueing(tmp_path, inspection_context, monkeypatch):
    import subprocess
    import sys
    import threading
    from dataclasses import replace

    from devtools_mcp_server import execution

    original_popen = subprocess.Popen
    original_queue = execution.queue.Queue
    observed = []
    enqueued = 0
    lock = threading.Lock()

    class MeteredQueue(original_queue):
        def put(self, item, *args, **kwargs):
            nonlocal enqueued
            label, chunk = item
            if label in {"stdout", "stderr"} and chunk:
                with lock:
                    enqueued += len(chunk)
                    observed.append(inspection_context.budget.totals.get("ordinary", 0) >= enqueued)
            return super().put(item, *args, **kwargs)

    def fake_cli(command, **kwargs):
        return original_popen(
            [
                sys.executable,
                "-c",
                "import sys;sys.stdout.write('visible\\n'*100);sys.stderr.write('warning\\n'*100)",
            ],
            **kwargs,
        )

    monkeypatch.setattr(execution.queue, "Queue", MeteredQueue)
    monkeypatch.setattr(execution.subprocess, "Popen", fake_cli)
    inspection_context.runtime = replace(inspection_context.runtime, git_executable=sys.executable)
    capture = execution.run_command("git", ("--version",), inspection_context)
    assert capture.returncode == 0
    assert observed and all(observed), "Uncharged bytes were buffered in the capture queue"


def test_absolute_current_directory_is_not_an_executable_search_root(tmp_path, monkeypatch):
    import os

    from devtools_mcp_server.execution import resolve_executable

    executable = tmp_path / ("git.exe" if os.name == "nt" else "git")
    executable.write_text("fixture executable")
    executable.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert resolve_executable("git") is None
