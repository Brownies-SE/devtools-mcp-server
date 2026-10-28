import importlib

import pytest

from devtools_mcp_server.models import InspectionError


def files():
    assert importlib.util.find_spec("devtools_mcp_server.tools.files"), "File inspectors missing"
    return importlib.import_module("devtools_mcp_server.tools.files")


def test_read_beyond_eof_is_empty(tmp_path, inspection_context):
    module = files()
    source = tmp_path / "app.py"
    source.write_text("print(1)\n")
    reply = module.read_file(
        module.FilesReadArgs(path=str(source), start_line=9, max_lines=1), inspection_context
    )
    assert reply.status == "ok"
    assert reply.data.lines == []


def test_whole_source_sanitized_before_excerpt_or_search(tmp_path, inspection_context):
    module = files()
    source = tmp_path / "app.py"
    source.write_text(
        "-----BEGIN PRIVATE KEY-----\nfixtureinteriorsecret\n-----END PRIVATE KEY-----\npublic.*text\n"
    )
    reply = module.read_file(
        module.FilesReadArgs(path=str(source), start_line=2, max_lines=1), inspection_context
    )
    assert reply.redacted
    assert reply.data.lines[0].line == 2
    assert "fixtureinteriorsecret" not in reply.model_dump_json()
    search = module.search_files(
        module.FilesSearchArgs(directory=str(tmp_path), query="fixtureinteriorsecret"),
        inspection_context,
    )
    assert search.data.matches == []
    search = module.search_files(
        module.FilesSearchArgs(directory=str(tmp_path), query=".*"), inspection_context
    )
    assert [(match.path, match.line) for match in search.data.matches] == [("app.py", 4)]


def test_recursion_globs_and_paging_exclude_secret_and_dependencies(tmp_path, inspection_context):
    module = files()
    for name in ["a.py", "b.py", ".env", "package.json"]:
        (tmp_path / name).write_text("public")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "skip.py").write_text("public")
    reply = module.list_files(
        module.FilesListArgs(directory=str(tmp_path), recursive=True, glob="*.py", limit=1),
        inspection_context,
    )
    assert [entry.path for entry in reply.data.entries] == ["a.py"]
    assert reply.next_cursor
    second = module.list_files(
        module.FilesListArgs(
            directory=str(tmp_path), recursive=True, glob="*.py", limit=1, cursor=reply.next_cursor
        ),
        inspection_context,
    )
    assert [entry.path for entry in second.data.entries] == ["b.py"]
    with pytest.raises(InspectionError):
        module.list_files(
            module.FilesListArgs(
                directory=str(tmp_path), recursive=True, glob="*.json", cursor=reply.next_cursor
            ),
            inspection_context,
        )


def test_search_omits_large_binary_and_denied_sources(tmp_path, inspection_context):
    module = files()
    (tmp_path / "app.py").write_text("hit\n")
    (tmp_path / "large.txt").write_bytes(b"x" * 1048577)
    (tmp_path / "binary.bin").write_bytes(b"hit\0")
    (tmp_path / ".env").write_text("hit hidden")
    reply = module.search_files(
        module.FilesSearchArgs(directory=str(tmp_path), query="hit"), inspection_context
    )
    assert reply.status == "partial"
    assert [match.path for match in reply.data.matches] == ["app.py"]
    assert reply.warnings
