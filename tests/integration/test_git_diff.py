import pytest
from pydantic import ValidationError
from test_git_metadata import commit, git, module
from test_git_metadata import unborn_repository as unborn_repository  # noqa: PLC0414 -- pytest fixture registration


def diff_module():
    m = module()
    assert callable(getattr(m, "git_diff", None)), "Safe Git diff inspector missing"
    return m


def test_working_staged_and_root_commit_are_separate(unborn_repository, inspection_context):
    m = diff_module()
    repo = unborn_repository
    commit(repo, "original\n")
    (repo / "app.py").write_text("staged\n", newline="\n")
    git(repo, "add", "app.py")
    (repo / "app.py").write_text("working\n", newline="\n")
    staged = m.git_diff(m.GitDiffArgs(repository=str(repo), mode="staged"), inspection_context)
    assert "+staged" in staged.data.files[0].patch
    assert "working" not in staged.data.files[0].patch
    working = m.git_diff(m.GitDiffArgs(repository=str(repo), mode="working"), inspection_context)
    assert "+working" in working.data.files[0].patch
    assert "original" not in working.data.files[0].patch
    root = m.git_diff(
        m.GitDiffArgs(repository=str(repo), mode="commit", revision="HEAD"), inspection_context
    )
    assert "+original" in root.data.files[0].patch


@pytest.fixture
def secret_rename_repo(unborn_repository):
    repo = unborn_repository
    commit(repo)
    (repo / ".env").write_text("FOO=unrecognized-secret\n", newline="\n")
    git(repo, "add", ".env")
    git(repo, "commit", "-m", "secret fixture")
    git(repo, "mv", ".env", "settings.txt")
    return repo


@pytest.mark.parametrize("modify", [False, True])
def test_destination_filter_cannot_expose_env_rename(
    secret_rename_repo, inspection_context, modify
):
    m = diff_module()
    repo = secret_rename_repo
    if modify:
        with (repo / "settings.txt").open("a") as stream:
            stream.write("extra\n")
        git(repo, "add", "settings.txt")
    reply = m.git_diff(
        m.GitDiffArgs(repository=str(repo), mode="staged", paths=["settings.txt"]),
        inspection_context,
    )
    assert "unrecognized-secret" not in reply.model_dump_json()
    assert reply.omitted_count >= 1
    assert reply.data.files == []
    assert reply.status == "partial"


def test_full_blob_redaction_precedes_hunk_selection(unborn_repository, inspection_context):
    m = diff_module()
    repo = unborn_repository
    body = (
        "-----BEGIN PRIVATE KEY-----\nfixture-private-interior\n-----END PRIVATE KEY-----\n"
        + "ordinary\n" * 20
    )
    commit(repo, body + "old\n")
    (repo / "app.py").write_text(body + "new\n", newline="\n")
    reply = m.git_diff(m.GitDiffArgs(repository=str(repo), mode="working"), inspection_context)
    assert reply.redacted
    assert "fixture-private-interior" not in reply.model_dump_json()
    assert "+new" in reply.data.files[0].patch


def test_change_only_to_redacted_value_preserves_changed_file(
    unborn_repository, inspection_context
):
    m = diff_module()
    repo = unborn_repository
    commit(repo, "password=first-fixture-value\n")
    (repo / "app.py").write_text("password=second-fixture-value\n", newline="\n")
    reply = m.git_diff(m.GitDiffArgs(repository=str(repo), mode="working"), inspection_context)
    assert reply.redacted
    assert len(reply.data.files) == 1
    assert reply.data.files[0].patch == ""
    assert "fixture-value" not in reply.model_dump_json()


def test_binary_and_protected_working_sources_are_omitted(
    unborn_repository, inspection_context, monkeypatch
):
    m = diff_module()
    repo = unborn_repository
    commit(repo)
    (repo / ".env").write_text("FOO=fixture-env-value")
    git(repo, "add", ".env")
    git(repo, "commit", "-m", "protected fixture")
    (repo / "app.py").write_bytes(b"binary\0new")
    (repo / ".env").write_text("FOO=changed-fixture-env")
    from devtools_mcp_server.paths import PathPolicy

    original = PathPolicy._open

    def guard(self, path, *args, **kwargs):
        assert path.name != ".env", "Protected working source opened"
        return original(self, path, *args, **kwargs)

    monkeypatch.setattr(PathPolicy, "_open", guard)
    reply = m.git_diff(m.GitDiffArgs(repository=str(repo), mode="working"), inspection_context)
    assert reply.status == "partial"
    assert reply.omitted_count >= 1
    assert reply.data.files == []


def test_unborn_staged_diff_uses_empty_baseline(unborn_repository, inspection_context):
    m = diff_module()
    (unborn_repository / "app.py").write_text("first\n", newline="\n")
    git(unborn_repository, "add", "app.py")
    reply = m.git_diff(
        m.GitDiffArgs(repository=str(unborn_repository), mode="staged"), inspection_context
    )
    assert "+first" in reply.data.files[0].patch


@pytest.mark.parametrize(
    "fields",
    [
        {"mode": "commit"},
        {"mode": "working", "revision": "HEAD"},
        {"mode": "working", "paths": ["../outside"]},
        {"mode": "staged", "paths": [":(glob)*"]},
    ],
)
def test_diff_inputs_reject_ambiguous_revision_or_paths(fields):
    m = diff_module()
    with pytest.raises(ValidationError):
        m.GitDiffArgs(repository="/fixture", **fields)


def test_duplicate_blob_identity_cannot_hide_protected_rename(
    unborn_repository, inspection_context
):
    m = diff_module()
    repo = unborn_repository
    commit(repo)
    for name in [".env", "ordinary.txt"]:
        (repo / name).write_text("FOO=unrecognized-secret\n", newline="\n")
    git(repo, "add", ".env", "ordinary.txt")
    git(repo, "commit", "-m", "duplicate source fixture")
    git(repo, "mv", "ordinary.txt", "first.txt")
    git(repo, "mv", ".env", "last.txt")
    reply = m.git_diff(
        m.GitDiffArgs(repository=str(repo), mode="staged", paths=["last.txt"]), inspection_context
    )
    assert "unrecognized-secret" not in reply.model_dump_json()
    assert reply.omitted_count >= 1
    assert reply.data.files == []
