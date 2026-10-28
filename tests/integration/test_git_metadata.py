import importlib
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from devtools_mcp_server.execution import resolve_executable
from devtools_mcp_server.models import InspectionError


def git(repo, *args):
    env = {
        key: os.environ[key]
        for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE")
        if key in os.environ
    }
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    return subprocess.check_output([resolve_executable("git"), "-C", str(repo), *args], env=env)


@pytest.fixture(params=["sha1", "sha256"])
def unborn_repository(tmp_path, inspection_context, request):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main", "--object-format=" + request.param)
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "core.autocrlf", "false")
    inspection_context.runtime = replace(
        inspection_context.runtime, git_executable=resolve_executable("git")
    )
    return repo


def module():
    assert importlib.util.find_spec("devtools_mcp_server.tools.git"), "Hardened Git adapter missing"
    return importlib.import_module("devtools_mcp_server.tools.git")


def commit(repo, text="old\n"):
    (repo / "app.py").write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "fixture subject")


def test_unborn_log_is_empty(unborn_repository, inspection_context):
    m = module()
    reply = m.git_log(m.GitLogArgs(repository=str(unborn_repository)), inspection_context)
    assert reply.status == "ok"
    assert reply.data.commits == []


def test_status_branches_and_log_are_read_only(unborn_repository, inspection_context):
    m = module()
    repo = unborn_repository
    commit(repo)
    (repo / "app.py").write_text("new\n", encoding="utf-8", newline="\n")
    (repo / "new file.py").write_text("untracked")
    (repo / ".gitignore").write_text("ignored.txt\n")
    (repo / "ignored.txt").write_text("ignored")
    before = (repo / ".git" / "index").read_bytes()
    reply = m.git_status(m.GitStatusArgs(repository=str(repo)), inspection_context)
    assert "app.py" in reply.data.unstaged
    assert "new file.py" in reply.data.untracked
    assert "ignored.txt" not in reply.data.untracked
    branches = m.git_branches(m.GitBranchesArgs(repository=str(repo)), inspection_context)
    assert [(row.name, row.current) for row in branches.data.branches] == [("main", True)]
    log = m.git_log(m.GitLogArgs(repository=str(repo)), inspection_context)
    assert log.data.commits[0].subject == "fixture subject"
    assert len(log.data.commits[0].commit) in {40, 64}
    assert (repo / ".git" / "index").read_bytes() == before


def test_git_status_does_not_read_tracked_env(unborn_repository, inspection_context, monkeypatch):
    m = module()
    repo = unborn_repository
    commit(repo)
    (repo / ".env").write_text("FOO=unrecognized-secret")
    git(repo, "add", ".env")
    from devtools_mcp_server.paths import PathPolicy

    original = PathPolicy._open

    def guarded(self, path, *args, **kwargs):
        assert Path(path).name != ".env", "Protected source was opened"
        return original(self, path, *args, **kwargs)

    monkeypatch.setattr(PathPolicy, "_open", guarded)
    reply = m.git_status(m.GitStatusArgs(repository=str(repo)), inspection_context)
    assert "unrecognized-secret" not in reply.model_dump_json()
    assert ".env" not in reply.data.staged


def test_git_clean_filter_is_never_executed(unborn_repository, inspection_context):
    m = module()
    repo = unborn_repository
    commit(repo)
    sentinel = repo / "should-not-exist"
    git(repo, "config", "filter.evil.clean", "touch " + str(sentinel))
    git(repo, "config", "filter.evil.process", "touch " + str(sentinel))
    git(repo, "config", "core.fsmonitor", "touch " + str(sentinel))
    (repo / ".gitattributes").write_text("app.py filter=evil\n")
    reply = m.git_status(m.GitStatusArgs(repository=str(repo)), inspection_context)
    assert reply.status == "partial"
    assert "app.py" not in reply.data.unstaged
    assert "app.py" in reply.data.unknown
    assert not sentinel.exists()


def test_git_includes_do_not_open_secret_sources(
    unborn_repository, inspection_context, monkeypatch
):
    m = module()
    repo = unborn_repository
    commit(repo)
    (repo / ".env").write_text("not even Git config")
    git(repo, "config", "include.path", "../.env")
    from devtools_mcp_server.paths import PathPolicy

    original = PathPolicy._open

    def guarded(self, path, *args, **kwargs):
        assert Path(path).name != ".env", "Git config include was followed"
        return original(self, path, *args, **kwargs)

    monkeypatch.setattr(PathPolicy, "_open", guarded)
    reply = m.git_log(m.GitLogArgs(repository=str(repo)), inspection_context)
    assert reply.data.commits[0].subject == "fixture subject"


@pytest.mark.parametrize("revision", ["--all", "HEAD~1", "HEAD:app.py", "main..other", "@{1}"])
def test_revision_expressions_rejected(unborn_repository, inspection_context, revision):
    m = module()
    with pytest.raises(InspectionError) as error:
        m.resolve_commit(str(unborn_repository), revision, inspection_context)
    assert error.value.code == "INVALID_ARGUMENT"


def test_linked_worktree_uses_verified_common_storage(
    unborn_repository, inspection_context, tmp_path
):
    m = module()
    commit(unborn_repository)
    worktree = tmp_path / "linked"
    git(unborn_repository, "worktree", "add", "-b", "linked", str(worktree))
    reply = m.git_log(m.GitLogArgs(repository=str(worktree)), inspection_context)
    assert reply.data.commits[0].subject == "fixture subject"


def test_missing_head_object_is_not_an_unborn_repository(unborn_repository, inspection_context):
    m = module()
    repo = unborn_repository
    commit(repo)
    oid = git(repo, "rev-parse", "HEAD").decode().strip()
    (repo / ".git" / "refs" / "heads" / "main").write_text("a" * len(oid) + "\n")
    with pytest.raises(InspectionError):
        m.git_log(m.GitLogArgs(repository=str(repo)), inspection_context)


def test_metadata_replacement_is_rejected_before_git_launch(unborn_repository, inspection_context):
    m = module()
    repo = unborn_repository
    commit(repo)
    with m.open_git_view(str(repo), inspection_context) as view:
        index = repo / ".git" / "index"
        index.write_bytes(index.read_bytes() + b"changed")
        with pytest.raises(InspectionError) as error:
            view.command("ls-files", "--stage", "-z")
        assert error.value.code == "ACCESS_DENIED"


def test_safe_external_ignore_file_is_applied(unborn_repository, inspection_context, tmp_path):
    m = module()
    repo = unborn_repository
    commit(repo)
    ignore = tmp_path / "safe-ignore"
    ignore.write_text("private-build.txt\n")
    git(repo, "config", "core.excludesfile", str(ignore))
    (repo / "private-build.txt").write_text("ignored")
    (repo / "visible.py").write_text("visible")
    reply = m.git_status(m.GitStatusArgs(repository=str(repo)), inspection_context)
    assert "private-build.txt" not in reply.data.untracked
    assert "visible.py" in reply.data.untracked


def test_invalid_symbolic_head_is_rejected(unborn_repository, inspection_context):
    m = module()
    (unborn_repository / ".git" / "HEAD").write_text("ref: ../.env\n")
    with pytest.raises(InspectionError) as error:
        m.git_branches(m.GitBranchesArgs(repository=str(unborn_repository)), inspection_context)
    assert error.value.code == "UNSUPPORTED"


def test_nested_alternate_store_cannot_hide_remote_storage(
    unborn_repository, inspection_context, tmp_path
):
    m = module()
    repo = unborn_repository
    commit(repo)
    alternate = tmp_path / "alternate-db"
    (alternate / "info").mkdir(parents=True)
    (alternate / "pack").mkdir()
    (alternate / "info" / "alternates").write_text("//remote-host/share/objects\n")
    (repo / ".git" / "objects" / "info" / "alternates").write_text(str(alternate) + "\n")
    with pytest.raises(InspectionError), m.open_git_view(str(repo), inspection_context):
        pass


def test_unused_filter_and_eol_settings_do_not_hide_changes(unborn_repository, inspection_context):
    m = module()
    repo = unborn_repository
    commit(repo)
    git(repo, "config", "filter.unused.clean", "this-command-must-not-run")
    git(repo, "config", "core.safecrlf", "false")
    git(repo, "config", "core.eol", "lf")
    (repo / "app.py").write_text("changed\n", newline="\n")
    reply = m.git_status(m.GitStatusArgs(repository=str(repo)), inspection_context)
    assert "app.py" in reply.data.unstaged
    assert "app.py" not in reply.data.unknown


def test_private_object_store_can_resolve_commit(unborn_repository, inspection_context):
    m = module()
    repo = unborn_repository
    commit(repo)
    oid = git(repo, "rev-parse", "HEAD").decode().strip()
    with m.open_git_view(str(repo), inspection_context) as view:
        result = view.command("cat-file", "-t", oid)
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
        assert result.stdout.strip() == b"commit"
