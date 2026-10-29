import importlib

import pytest

from devtools_mcp_server.models import InspectionError


def test_metadata_reader_cannot_open_arbitrary_keys(tmp_path, inspection_context):
    from devtools_mcp_server import paths

    reader = getattr(paths, "read_git_metadata", None)
    assert callable(reader), "Enumerated Git metadata access missing"
    for key in ["../.env", "credentials", "objects/aa/blob", "/etc/passwd"]:
        with pytest.raises(InspectionError):
            reader(str(tmp_path), key, inspection_context)


def test_git_view_interface_is_available():
    assert importlib.util.find_spec("devtools_mcp_server.tools.git"), "Private Git view missing"
