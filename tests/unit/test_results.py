import pytest
from pydantic import BaseModel

from devtools_mcp_server import results
from devtools_mcp_server.models import CaptureBudget, InspectionError, Reply


class TextData(BaseModel):
    text: str


def render(reply):
    function = getattr(results, "to_mcp_result", None)
    assert callable(function), "complete-result filtering is missing"
    return function(reply)


def test_result_includes_all_channels_in_64k_budget():
    result = render(Reply[TextData](data=TextData(text=("visible λ\n" * 20000))))
    wire = result.model_dump_json(by_alias=True, exclude_unset=True).encode()
    assert len(wire) <= 65536
    assert result.structured_content["truncated"]
    assert result.structured_content["status"] == "partial"


def test_result_redacts_both_representations():
    result = render(Reply[TextData](data=TextData(text="api_key=fixture-secret-12345")))
    assert "fixture-secret-12345" not in result.model_dump_json(by_alias=True)
    assert result.structured_content["redacted"]


def test_protocol_error_keeps_correlation_but_never_reflects_values():
    function = getattr(results, "sanitize_protocol_error", None)
    assert callable(function), "protocol error filtering is missing"
    message = {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {"code": -32602, "message": "fixture-secret", "data": {"input": "fixture-secret"}},
    }
    result = function(message)
    assert result["id"] == 7
    assert result["error"]["code"] == -32602
    assert "fixture-secret" not in str(result)


def test_budget_has_independent_sources_and_shared_ceiling():
    budget = CaptureBudget()
    assert callable(getattr(budget, "consume", None)), "capture accounting is missing"
    budget.consume("git_blob", 1048576, source="old")
    budget.consume("git_blob", 1048576, source="new")
    with pytest.raises(InspectionError):
        budget.consume("git_blob", 1, source="old")
    budget = CaptureBudget()
    budget.consume("ordinary", 1048576)
    with pytest.raises(InspectionError):
        budget.consume("ordinary", 1)
