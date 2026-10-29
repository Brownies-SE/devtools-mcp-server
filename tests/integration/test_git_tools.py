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
