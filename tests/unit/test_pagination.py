import base64
import importlib
import json

import pytest

from devtools_mcp_server.models import InspectionError


def pagination():
    assert importlib.util.find_spec("devtools_mcp_server.pagination"), "Bounded cursors missing"
    return importlib.import_module("devtools_mcp_server.pagination")


def test_cursor_contains_only_filter_digest_and_numeric_position():
    module = pagination()
    token = module.encode_cursor(
        "files_list", {"directory": "/fixture-secret-path"}, module.PagePosition(offset=2)
    )
    payload = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    assert "fixture-secret-path" not in json.dumps(payload)
    assert (
        module.decode_cursor(token, "files_list", {"directory": "/fixture-secret-path"}).offset == 2
    )
    for tool, filters in [
        ("files_read", {"directory": "/fixture-secret-path"}),
        ("files_list", {"directory": "/other"}),
    ]:
        with pytest.raises(InspectionError):
            module.decode_cursor(token, tool, filters)


@pytest.mark.parametrize("value", ["garbage", "x" * 4097, "e30", "", "eyJvZmZzZXQiOi0xfQ"])
def test_invalid_cursors_denied(value):
    module = pagination()
    with pytest.raises(InspectionError):
        module.decode_cursor(value, "files_list", {})
