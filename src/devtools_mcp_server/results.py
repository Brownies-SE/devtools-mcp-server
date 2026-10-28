import json
from dataclasses import replace

from anyio.abc import ObjectSendStream
from mcp import types
from mcp.shared.message import SessionMessage
from pydantic import ValidationError

from .models import Reply
from .redaction import KEY_NAME, REDACTED, sanitize_text


def _sanitize(value):
    if isinstance(value, str):
        clean = sanitize_text(value)
        return clean.text, clean.redacted
    if isinstance(value, list):
        cleaned = [_sanitize(item) for item in value]
        return [item[0] for item in cleaned], any(item[1] for item in cleaned)
    if isinstance(value, dict):
        result = {}
        changed = False
        for key, item in value.items():
            if KEY_NAME.fullmatch(key):
                result[key] = REDACTED
                changed = True
            else:
                result[key], flag = _sanitize(item)
                changed |= flag
        return result, changed
    return value, False


def _result(payload):
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
        is_error=payload["status"] == "error",
    )


def _shrink(value):
    candidates = []

    def find(node):
        if isinstance(node, dict):
            for key, item in node.items():
                if isinstance(item, str) and item:
                    candidates.append((len(item), node, key))
                elif isinstance(item, list) and item:
                    candidates.append((len(json.dumps(item)), node, key))
                elif isinstance(item, dict):
                    find(item)
        elif isinstance(node, list):
            for item in node:
                find(item)

    find(value)
    if not candidates:
        return False
    _, parent, key = max(candidates, key=lambda item: item[0])
    item = parent[key]
    if isinstance(item, list):
        parent[key] = item[: len(item) // 2]
    else:
        lines = item.splitlines(keepends=True)
        parent[key] = "".join(lines[: len(lines) // 2])
    return True


def to_mcp_result(reply: Reply) -> types.CallToolResult:
    payload, redacted = _sanitize(reply.model_dump(mode="json"))
    payload["redacted"] |= redacted
    while True:
        result = _result(payload)
        if len(result.model_dump_json(by_alias=True, exclude_unset=True).encode()) <= 65536:
            break
        payload["truncated"] = True
        payload["next_cursor"] = None
        payload["omitted_count"] = None
        if payload["status"] != "error":
            payload["status"] = "partial"
        payload["warnings"] = [
            {"code": "OUTPUT_LIMIT", "message": "Result clipped; narrow the query."}
        ]
        if not _shrink(payload.get("data")):
            payload["data"] = None
            payload["status"] = "error"
            payload["error"] = {
                "code": "OUTPUT_LIMIT",
                "message": "Result exceeds the output limit.",
            }
            break
    try:
        reply.__class__.model_validate(payload)
    except ValidationError:
        payload["data"] = None
        payload["status"] = "error"
        payload["error"] = {
            "code": "OUTPUT_LIMIT",
            "message": "Result cannot be safely represented.",
        }
    return _result(payload)


def sanitize_protocol_error(message: dict) -> dict:
    if "error" not in message:
        return message
    return {
        "jsonrpc": "2.0",
        "id": message.get("id"),
        "error": {"code": message["error"]["code"], "message": "Invalid protocol request."},
    }


class SafeSendStream(ObjectSendStream):
    def __init__(self, stream):
        self.stream = stream

    async def send(self, message: SessionMessage) -> None:
        if isinstance(message.message, types.JSONRPCError):
            safe = sanitize_protocol_error(message.message.model_dump(by_alias=True))
            message = replace(message, message=types.JSONRPCError.model_validate(safe))
        await self.stream.send(message)

    async def aclose(self) -> None:
        await self.stream.aclose()
