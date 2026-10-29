import pytest
from pydantic import ValidationError

from devtools_mcp_server import models
from devtools_mcp_server.tools.git import GitStatusArgs


def test_page_limits_are_strict():
    page_args = getattr(models, "PageArgs", None)
    assert page_args is not None, "strict pagination contract is not implemented"
    assert page_args().limit == 100
    for invalid in ("100", True, 0, 201):
        with pytest.raises(ValidationError):
            page_args(limit=invalid)


def test_unknown_arguments_are_rejected():
    with pytest.raises(ValidationError):
        GitStatusArgs(repository="/fixture", extra="fixture-secret")
