from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Code = Literal[
    "ACCESS_DENIED",
    "SECRET_BLOCKED",
    "DEPENDENCY_MISSING",
    "UNAVAILABLE",
    "UNSUPPORTED",
    "INVALID_ARGUMENT",
    "NOT_FOUND",
    "TIMEOUT",
    "OUTPUT_LIMIT",
    "BUSY",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PageArgs(StrictModel):
    limit: int = Field(default=100, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)


class EnvironmentArgs(StrictModel):
    pass


class ToolError(StrictModel):
    code: Code
    message: str


class Warning(ToolError):
    pass


class Reply[T](StrictModel):
    status: Literal["ok", "partial", "error"] = "ok"
    observed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    data: T | None = None
    warnings: list[Warning] = Field(default_factory=list)
    truncated: bool = False
    redacted: bool = False
    omitted_count: int | None = Field(default=None, ge=0)
    next_cursor: str | None = None
    error: ToolError | None = None


class InspectionError(Exception):
    def __init__(self, code: Code, message: str):
        self.code = code
        self.message = message
        super().__init__(code)


@dataclass(frozen=True)
class StartupConfig:
    extra_secret_paths: tuple[str, ...] = ()
    extra_health_paths: tuple[str, ...] = ()
    docker_endpoint: str | None = None


@dataclass(frozen=True)
class RuntimeState:
    git_executable: str | None = None
    docker_executable: str | None = None
    docker_endpoint: str | None = None
    docker_error: ToolError | None = None


@dataclass
class CaptureBudget:
    totals: dict[str, int] = field(default_factory=dict)
    sources: dict[tuple[str, str], int] = field(default_factory=dict)
    entries: int = 0


@dataclass
class InspectionContext:
    config: StartupConfig
    deadline: float
    budget: CaptureBudget
    runtime: RuntimeState
    scratch_dir: Path


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    input_model: type[StrictModel]
    data_model: type[BaseModel]
    handler: Callable
    description: str
