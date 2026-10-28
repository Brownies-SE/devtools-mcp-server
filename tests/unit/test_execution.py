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
        ("docker", ("container", "logs", "--follow", "abc")),
        ("docker", ("container", "restart", "abc")),
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
