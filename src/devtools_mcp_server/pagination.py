import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, ValidationError

from .models import InspectionError, StrictModel


@dataclass(frozen=True)
class PagePosition:
    offset: int = 0
    anchor_commit: str | None = None


class Cursor(StrictModel):
    version: Literal[1] = 1
    tool: str = Field(max_length=64)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    offset: int = Field(ge=0, le=2147483647)
    anchor_commit: str | None = Field(default=None, pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _fingerprint(filters):
    return hashlib.sha256(
        json.dumps(filters, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def encode_cursor(tool: str, filters: dict, position: PagePosition) -> str:
    value = Cursor(
        tool=tool,
        fingerprint=_fingerprint(filters),
        offset=position.offset,
        anchor_commit=position.anchor_commit,
    )
    return base64.urlsafe_b64encode(value.model_dump_json().encode()).decode().rstrip("=")


def decode_cursor(value: str, tool: str, filters: dict) -> PagePosition:
    try:
        if not value or len(value) > 4096 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        cursor = Cursor.model_validate_json(raw)
        if cursor.tool != tool or cursor.fingerprint != _fingerprint(filters):
            raise ValueError
        return PagePosition(cursor.offset, cursor.anchor_commit)
    except ValueError, ValidationError, binascii.Error:
        raise InspectionError(
            "INVALID_ARGUMENT", "Cursor is invalid or belongs to another query."
        ) from None


def page(items: list, args, tool: str, filters: dict):
    position = (
        decode_cursor(args.cursor, tool, filters) if args.cursor is not None else PagePosition()
    )
    selected = items[position.offset : position.offset + args.limit]
    offset = position.offset + len(selected)
    cursor = (
        encode_cursor(tool, filters, PagePosition(offset, position.anchor_commit))
        if offset < len(items)
        else None
    )
    return selected, cursor
