import fnmatch
from datetime import UTC, datetime
from pathlib import PurePosixPath

from pydantic import Field, field_validator

from ..models import InspectionError, PageArgs, Reply, StrictModel, Warning
from ..pagination import page
from ..paths import PathPolicy
from ..redaction import sanitize_text


class DirectoryArgs(PageArgs):
    directory: str = Field(min_length=1, max_length=4096)
    glob: str | None = Field(default=None, max_length=4096)

    @field_validator("glob")
    @classmethod
    def confined_glob(cls, value):
        if value is not None and (
            PurePosixPath(value).is_absolute()
            or ".." in PurePosixPath(value).parts
            or "\\" in value
            or "\0" in value
        ):
            raise ValueError("Glob must match relative paths.")
        return value


class FilesListArgs(DirectoryArgs):
    recursive: bool = False


class FilesSearchArgs(DirectoryArgs):
    query: str = Field(min_length=1, max_length=1024)


class FilesReadArgs(StrictModel):
    path: str = Field(min_length=1, max_length=4096)
    start_line: int = Field(default=1, ge=1, le=2147483647)
    max_lines: int = Field(default=200, ge=1, le=1000)


class FileEntry(StrictModel):
    path: str
    type: str
    size_bytes: int
    modified_at: str


class FileLine(StrictModel):
    line: int
    text: str


class FileMatch(FileLine):
    path: str


class FilesListData(StrictModel):
    entries: list[FileEntry]


class FilesReadData(StrictModel):
    lines: list[FileLine]


class FilesSearchData(StrictModel):
    matches: list[FileMatch]


def _warning(error):
    return Warning(code=error.code, message=error.message)


def _entries(policy, args, ctx, recursive, warnings):
    try:
        for entry in policy.iter_entries(args.directory, recursive, ctx):
            if args.glob is None or fnmatch.fnmatchcase(entry.relative_path, args.glob):
                yield entry
    except InspectionError as error:
        if error.code not in {"OUTPUT_LIMIT", "TIMEOUT"}:
            raise
        warnings.append(_warning(error))


def list_files(args: FilesListArgs, ctx) -> Reply[FilesListData]:
    warnings = []
    entries = [
        FileEntry(
            path=entry.relative_path,
            type=entry.type,
            size_bytes=entry.size,
            modified_at=datetime.fromtimestamp(entry.modified_at, UTC).isoformat(),
        )
        for entry in _entries(PathPolicy(ctx.config), args, ctx, args.recursive, warnings)
    ]
    entries.sort(key=lambda entry: entry.path)
    selected, cursor = page(
        entries, args, "files_list", args.model_dump(exclude={"cursor", "limit"})
    )
    return Reply[FilesListData](
        data=FilesListData(entries=selected),
        status="partial" if warnings else "ok",
        warnings=warnings,
        truncated=bool(warnings),
        next_cursor=None if warnings else cursor,
    )


def read_file(args: FilesReadArgs, ctx) -> Reply[FilesReadData]:
    with PathPolicy(ctx.config).open_text(args.path, ctx) as source:
        clean = sanitize_text(source.read_utf8())
    lines = clean.text.splitlines()
    start = args.start_line - 1
    chosen = lines[start : start + args.max_lines]
    return Reply[FilesReadData](
        data=FilesReadData(
            lines=[FileLine(line=start + index + 1, text=text) for index, text in enumerate(chosen)]
        ),
        redacted=clean.redacted,
        truncated=start + len(chosen) < len(lines),
    )


def search_files(args: FilesSearchArgs, ctx) -> Reply[FilesSearchData]:
    policy = PathPolicy(ctx.config)
    warnings = []
    matches = []
    redacted = False
    for entry in _entries(policy, args, ctx, True, warnings):
        if entry.type == "directory":
            continue
        try:
            with policy.open_text(str(entry.path), ctx) as source:
                clean = sanitize_text(source.read_utf8())
        except InspectionError as error:
            warnings.append(_warning(error))
            if ctx.budget.totals.get("file_scan", 0) >= 20 * 1048576:
                break
            continue
        redacted |= clean.redacted
        matches.extend(
            FileMatch(path=entry.relative_path, line=number, text=text)
            for number, text in enumerate(clean.text.splitlines(), 1)
            if args.query in text
        )
        if len(matches) >= 10000:
            warnings.append(
                Warning(code="OUTPUT_LIMIT", message="Match limit reached; narrow the query.")
            )
            break
    matches.sort(key=lambda match: (match.path, match.line))
    selected, cursor = page(
        matches, args, "files_search", args.model_dump(exclude={"cursor", "limit"})
    )
    return Reply[FilesSearchData](
        data=FilesSearchData(matches=selected),
        status="partial" if warnings else "ok",
        warnings=warnings[:10],
        redacted=redacted,
        truncated=bool(warnings),
        next_cursor=None if warnings else cursor,
    )
