import pytest
from test_git_metadata import commit, git, module
from test_git_metadata import unborn_repository as unborn_repository  # noqa: PLC0414


def test_show_returns_commit_details_and_safe_patch(unborn_repository, inspection_context):
    m = module()
    assert hasattr(m, "git_show"), "git_show is missing"
    commit(unborn_repository)
    git(
        unborn_repository,
        "commit",
        "--allow-empty",
        "-m",
        "second subject\n\npassword=fixture-secret",
    )
    reply = m.git_show(m.GitShowArgs(repository=str(unborn_repository)), inspection_context)
    assert reply.data.commit.subject == "second subject"
    assert len(reply.data.parents) == 1
    assert "fixture-secret" not in reply.model_dump_json()
    assert reply.redacted


def test_show_root_commit_has_patch(unborn_repository, inspection_context):
    m = module()
    assert hasattr(m, "git_show"), "git_show is missing"
    commit(unborn_repository)
    reply = m.git_show(m.GitShowArgs(repository=str(unborn_repository)), inspection_context)
    assert reply.data.parents == []
    assert "+old" in reply.data.files[0].patch


def test_changed_files_never_reads_patch_blobs(unborn_repository, inspection_context, monkeypatch):
    m = module()
    assert hasattr(m, "git_changed_files"), "git_changed_files is missing"
    commit(unborn_repository)
    (unborn_repository / "app.py").write_bytes(b"binary\0new")
    git(unborn_repository, "add", "app.py")

    def forbidden(*args):
        raise AssertionError("Changed-file metadata must not read blobs")

    monkeypatch.setattr(m, "_blob", forbidden)
    reply = m.git_changed_files(
        m.GitDiffArgs(repository=str(unborn_repository), mode="staged"), inspection_context
    )
    assert [(row.old_path, row.new_path, row.status) for row in reply.data.files] == [
        ("app.py", "app.py", "modified")
    ]
    assert "patch" not in reply.data.model_dump_json()


def test_ignored_directory_is_pruned_before_open(
    unborn_repository, inspection_context, monkeypatch
):
    from devtools_mcp_server.paths import PathPolicy

    m = module()
    commit(unborn_repository)
    (unborn_repository / ".gitignore").write_text("node_modules/\n")
    generated = unborn_repository / "node_modules"
    generated.mkdir()
    (generated / "package.js").write_text("generated")
    (unborn_repository / "app.py").write_text("changed\n")
    original = PathPolicy._open

    def guarded(self, path, *args, **kwargs):
        assert path.name != "node_modules", "Ignored directory was opened"
        return original(self, path, *args, **kwargs)

    monkeypatch.setattr(PathPolicy, "_open", guarded)
    reply = m.git_status(m.GitStatusArgs(repository=str(unborn_repository)), inspection_context)
    assert reply.data.unstaged == ["app.py"]
    assert not any("node_modules" in name for name in reply.data.untracked)


def test_submodule_worktree_is_not_parent_untracked_content(unborn_repository, inspection_context):
    m = module()
    commit(unborn_repository)
    oid = git(unborn_repository, "rev-parse", "HEAD").decode().strip()
    git(unborn_repository, "update-index", "--add", "--cacheinfo", f"160000,{oid},vendor")
    nested = unborn_repository / "vendor"
    nested.mkdir()
    (nested / "nested.py").write_text("submodule source")
    reply = m.git_status(m.GitStatusArgs(repository=str(unborn_repository)), inspection_context)
    assert not any(name.startswith("vendor/") for name in reply.data.untracked)
    assert "vendor" in reply.data.unknown


def test_ignored_filemode_preserves_index_mode(unborn_repository, inspection_context):
    m = module()
    commit(unborn_repository)
    git(unborn_repository, "update-index", "--chmod=+x", "app.py")
    git(unborn_repository, "commit", "-m", "executable fixture")
    git(unborn_repository, "config", "core.filemode", "false")
    status = m.git_status(m.GitStatusArgs(repository=str(unborn_repository)), inspection_context)
    assert status.data.unstaged == []
    changed = m.git_changed_files(
        m.GitDiffArgs(repository=str(unborn_repository)), inspection_context
    )
    assert changed.data.files == []


def test_permission_mode_change_is_modified_not_type_change(unborn_repository, inspection_context):
    m = module()
    commit(unborn_repository)
    git(unborn_repository, "update-index", "--chmod=+x", "app.py")
    reply = m.git_changed_files(
        m.GitDiffArgs(repository=str(unborn_repository), mode="staged"), inspection_context
    )
    assert reply.data.files[0].status == "modified"


@pytest.mark.parametrize("tool", ["git_status", "git_diff", "git_changed_files"])
def test_unreadable_file_preserves_other_changes(
    unborn_repository, inspection_context, monkeypatch, tool
):
    import errno

    from devtools_mcp_server.paths import PathPolicy

    m = module()
    commit(unborn_repository)
    denied = unborn_repository / "blocked.py"
    denied.write_text("original\n")
    git(unborn_repository, "add", "blocked.py")
    git(unborn_repository, "commit", "-m", "second tracked file")
    (unborn_repository / "app.py").write_text("changed\n")
    original = PathPolicy._open

    def guarded(self, path, *args, **kwargs):
        if path.name == "blocked.py":
            raise PermissionError(errno.EACCES, "fixture access denied")
        return original(self, path, *args, **kwargs)

    monkeypatch.setattr(PathPolicy, "_open", guarded)
    args = (
        m.GitStatusArgs(repository=str(unborn_repository))
        if tool == "git_status"
        else m.GitDiffArgs(repository=str(unborn_repository))
    )
    reply = getattr(m, tool)(args, inspection_context)
    assert reply.status == "partial"
    assert any(warning.code == "ACCESS_DENIED" for warning in reply.warnings)
    if tool == "git_status":
        assert "app.py" in reply.data.unstaged
        assert "blocked.py" in reply.data.unknown
    else:
        assert any(row.new_path == "app.py" for row in reply.data.files)


def test_safe_untracked_symlink_is_reported_without_target_read(
    unborn_repository, inspection_context, monkeypatch, tmp_path
):
    from devtools_mcp_server.paths import PathPolicy

    m = module()
    commit(unborn_repository)
    target = tmp_path / "symlink-target.txt"
    target.write_text("outside source contents")
    alias = unborn_repository / "alias.txt"
    try:
        alias.symlink_to(target)
    except OSError, NotImplementedError:
        pytest.skip("Native symlink creation is unavailable")
    original = PathPolicy._open

    def guarded(self, path, *args, **kwargs):
        assert path.name != "symlink-target.txt", "Untracked symlink target was opened"
        return original(self, path, *args, **kwargs)

    monkeypatch.setattr(PathPolicy, "_open", guarded)
    reply = m.git_status(m.GitStatusArgs(repository=str(unborn_repository)), inspection_context)
    assert "alias.txt" in reply.data.untracked
