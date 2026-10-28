# DevTools MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, read-only Python 3.14 MCP server that exposes the reviewed 15-tool interface on Windows, macOS, and Linux while excluding known secret sources and bounding all inspection work.

**Architecture:** Use the official MCP SDK for stdio and explicit input/output schemas. A small asynchronous parent validates requests, admits at most four calls, and starts one isolated worker per inspection; each worker uses the shared path, secret, capture, and domain policies. Parent-owned process containment enforces deadlines even when filesystem or OS APIs block; no worker may execute project code.

**Tech Stack:** Standard CPython 3.14; official `mcp` SDK, `psutil`, and explicitly declared `pydantic` for strict schemas. Use `pytest`, its AnyIO support, and `ruff` for development; `uv.lock` records the resolved dependencies. Do not install anything or create product code while executing this document's planning stage.

**Spec:** [Reviewed design](../specs/2026-10-28-devtools-mcp-server-design.md). Read the entire spec before executing a task; this plan does not relax its restrictions. Runtime package versions are selected and locked in Task 1, with compatibility checked on each target platform.

## Global Constraints

- Implement in Python 3.14 with the official MCP Python SDK.
- Support Windows, macOS, and Linux in v1.
- Direct launch with the virtual environment’s absolute Python path and `-m devtools_mcp_server`.
- stdio only. Standard output contains MCP messages exclusively; bounded, sanitized operational logs go to standard error.
- At most 4 concurrent tool calls, no waiting queue; per-call deadline 10 seconds including result construction and filtering.
- Default 100 items per page, hard ceiling 200; default 200 file/log lines, hard ceiling 1,000.
- File content: 1 MiB per file; traversal: 10,000 candidates and 20 MiB scanned content per call, plus deadline.
- Tool result: 64 KiB UTF-8 serialized MCP result including text, structured content, escaping, and metadata; JSON-RPC envelope excluded.
- Ordinary subprocess stdout+stderr: 1 MiB aggregate per call; Git blob stdout has its separate 20 MiB aggregate and 1 MiB-per-blob limits, while blob stderr uses the ordinary cap.
- HTTP: 5 seconds, 16 KiB response headers, 1 MiB wire body, no compression, proxy inheritance, DNS, retries, credentials, query strings, or redirects.
- Known secret sources are denied before content reads; sanitize full bounded file content before selecting excerpts. Keep ordinary project configuration readable.
- No arbitrary shell, mutation tools, application execution, credential retrieval, elevation, remote targets, telemetry, or runtime dependency downloads.
- Launch options only extend secret exclusions and health paths or select a local Docker endpoint. Tool calls cannot change policy.
- Native acceptance targets: Windows x86-64, macOS arm64, Linux x86-64 on standard CPython 3.14; WSL is a separate Linux environment.
- Local Python installation/upgrades are operational work and are excluded from the implementation tasks.

## Review Focus

These five likely failure modes are explicitly assigned tests below, in addition to the spec's examples:

1. An application logs a key across several chunks, or a requested excerpt starts inside it: no secret fragment reaches an MCP channel (Tasks 2, 6, 10).
2. A path changes into a network mount, reparse point, or cloud placeholder during inspection: reject the changed target without hydration or remote access (Tasks 4–5).
3. Cancellation happens after a helper creates its Git/Docker child: reap only owned processes and free capacity, with no effect on the inspected application (Task 3).
4. A live directory/process list changes between cursor pages: validate cursor scope, bound work, and explicitly preserve partial/unknown semantics (Tasks 6–7).
5. A repo uses unusual filenames, SHA-256 object IDs, an unborn branch, or a secret rename with a destination-only filter: preserve paths and deny protected content without invoking helpers (Tasks 8–9).

## File and interface map

All paths below are new unless a task explicitly says Modify. There is currently no application code.

| Path | Responsibility |
| --- | --- |
| `pyproject.toml`, `uv.lock`, `.gitignore` | Python 3.14 metadata, dependencies, local environment/cache exclusions, pytest/ruff configuration |
| `src/devtools_mcp_server/__init__.py`, `__main__.py` | Package and stdio entry point; no import-time inspection |
| `src/devtools_mcp_server/server.py` | SDK adapter, tool discovery/dispatch, validation and MCP result mapping |
| `src/devtools_mcp_server/models.py`, `config.py` | Strict common schemas, limits and operator startup options |
| `src/devtools_mcp_server/redaction.py` | Bounded complete-text and streamed-record secret filtering |
| `src/devtools_mcp_server/results.py`, `pagination.py` | Sanitize all channels, budget the entire MCP result, stateless cursor validation |
| `src/devtools_mcp_server/supervisor.py`, `worker.py` | Four-call admission, deadlines, private worker IPC and domain dispatch |
| `src/devtools_mcp_server/execution.py` | Fixed executable adapters, minimal environments and incremental capture budgets |
| `src/devtools_mcp_server/platforms/{__init__,posix,windows}.py` | Opened-handle path/locality checks and owned-process containment |
| `src/devtools_mcp_server/paths.py` | Central deny policy, traversal rules, verified file access facade |
| `src/devtools_mcp_server/tools/{__init__,environment,files,processes,system,git,docker,http}.py` | Domain request/data models and inspection functions |
| `tests/unit/`, `tests/integration/`, `tests/platform/`, `tests/fixtures/` | Focused unit, private-IPC/MCP, native-OS and deterministic adversarial fixtures |
| `README.md`, `docs/{tools,security,troubleshooting,validation}.md` | Direct launch examples, schemas, policy, walkthrough and actual validation evidence |

Avoid extra adapters/frameworks. Platform separation exists because kernel handle APIs differ; domain functions otherwise share code. No diagnostic aggregator, database, watcher, HTTP server or plugin loader.

### Shared contracts decided here

- `StrictModel`: Pydantic model with `extra='forbid'`, strict validation. `Reply[T]`: `status`, UTC `observed_at`, nullable typed `data`, `warnings: list[Warning]`, `truncated: bool`, `redacted: bool`, `omitted_count: int | None`, `next_cursor: str | None`, `error: ToolError | None`. `ToolError` and `Warning` carry a stable code and sanitized message; never raw exception text.
- Codes: `ACCESS_DENIED`, `SECRET_BLOCKED`, `DEPENDENCY_MISSING`, `UNAVAILABLE`, `UNSUPPORTED`, `INVALID_ARGUMENT`, `NOT_FOUND`, `TIMEOUT`, `OUTPUT_LIMIT`, `BUSY`. Useful partial results are not MCP errors; failed tools are.
- `StartupConfig(extra_secret_paths: tuple[str, ...] = (), extra_health_paths: tuple[str, ...] = (), docker_endpoint: str | None = None)`: immutable, no environment-file loading. CLI options: repeated `--exclude-secret-path`, repeated `--allow-health-path`, and `--docker-endpoint`; no policy-disabling options.
- `InspectionContext(config: StartupConfig, deadline: float, budget: CaptureBudget)`: worker-local, not sent to the agent. `CaptureBudget.consume(channel: Literal['ordinary', 'git_blob', 'file_scan'], amount: int) -> None` enforces all aggregate ceilings and per-source limits at read time.
- `ToolDefinition(name: str, input_model: type[StrictModel], data_model: type[BaseModel], handler: Callable)`: server/worker registry entry. Each domain defines its own models; `tools/__init__.py` contains only explicit registration, not dynamic imports from projects.
- `InspectionError(code: str, message: str)` is the internal typed failure mapped to a sanitized `ToolError`; never expose raw exception values. `PageArgs(limit: int = 100, cursor: str | None = None)` supplies strict 1–200 pagination validation.
- Domain handlers are synchronous inside an isolated worker: `handle(args: DomainArgs, ctx: InspectionContext) -> Reply[DomainData]`. The parent exposes `await Supervisor.call(name: str, arguments: dict[str, JsonValue]) -> Reply`; SDK serialization uses `to_mcp_result(reply: Reply) -> CallToolResult`.
- Tests invoke async code with pytest's AnyIO plugin, asyncio backend only. Commands use `uv run --frozen ...` in the development environment; shipped client examples invoke `.venv` Python directly. Unit tests never use actual credentials, whole home directories, public HTTP endpoints, or application mutation.

## Task 1: A running stdio server with one validated inspection tool

**Files:** Create package/entry point, `server.py`, `models.py`, `config.py`, `tools/{__init__,environment}.py`, `pyproject.toml`, `uv.lock`, `.gitignore`, `tests/conftest.py`, `tests/integration/test_stdio.py`, `tests/unit/test_models.py`.

**Interfaces:** Produce `StrictModel`, `Reply[T]`, `ToolError`, `Warning`, `StartupConfig`, `ToolDefinition`, `create_server(config: StartupConfig) -> Server`, `async run_stdio(config: StartupConfig) -> None`, and `inspect_environment(args: EnvironmentArgs, ctx: InspectionContext) -> Reply[EnvironmentData]`. Define `InspectionContext`, `CaptureBudget`, `InspectionError` and `PageArgs` now with the fields/signatures above; Task 2 adds budget enforcement and Task 3 adds isolated execution. Add an `inspection_context` fixture to `tests/conftest.py` with empty startup options, a fresh capture budget and a monotonic 10-second deadline. Environment data starts with OS/Python/server versions and feature availability; Task 7 completes dependency/limit reporting.

- [ ] **Write tests and minimal test setup:** Create metadata, an empty package and the named test files, then run `uv lock` and `uv sync --frozen` so test failures are about missing behavior rather than an unavailable environment. Use `uv` as a development-tool prerequisite, provisioned separately if absent; do not install or upgrade machine Python in these tasks. SDK client initialization and `tools/list` expose only implemented tools; `environment_info` returns Python 3.14 and valid structured+text content; successful and error replies validate against the published output schema. Strict input tests assert unknown keys, `limit='100'`, bool-as-int, and out-of-range limits yield `INVALID_ARGUMENT`, with no raw validation value in output. Use an SDK client fixture, with a 10-second outer test timeout, connected to an actual subprocess launched with `-I -m devtools_mcp_server` after local package installation.

Core assertion for the named test (create its test-only fixtures in this task):

```python
def test_page_limit_rejects_coercion():
    with pytest.raises(ValidationError):
        PageArgs(limit='100')
```

- [ ] **Run red:** `uv run pytest tests/integration/test_stdio.py tests/unit/test_models.py -q`; expect missing package/handler assertions before implementation. The setup step resolves and locks current stable Python-3.14-compatible packages, declares `requires-python='>=3.14,<3.15'`, explicitly declares Pydantic, and pins all build/dev dependencies in `uv.lock`; use the official SDK v2 APIs cited below. No CLI extra or dotenv loader.
- [ ] **Implement:** Minimal explicit SDK tool discovery/dispatch and stdio transport, no HTTP listener. Use constructor-based low-level `Server` handlers to control `CallToolResult`, schemas, validation failures and errors directly. `__main__` guards process startup and produces no import-time stdout. Define common reply invariants and startup argument validation; discovery includes `readOnlyHint=true` and describes returned text as untrusted evidence. Do not advertise unfinished domain tools.
- [ ] **Run green:** `uv run --frozen pytest tests/integration/test_stdio.py tests/unit/test_models.py -q`; all assertions pass, stdout parses entirely as MCP protocol messages. Empty stdin/normal client close shuts down cleanly.
- [ ] **Commit:** `git add pyproject.toml uv.lock .gitignore src/devtools_mcp_server tests/conftest.py tests/integration/test_stdio.py tests/unit/test_models.py` then `git commit -m "feat: bootstrap validated local stdio inspection server"`.

## Task 2: Shared secret filtering and complete-result budgets

**Files:** Create `redaction.py`, `results.py`, `tests/unit/test_redaction.py`, `tests/unit/test_results.py`; Modify `models.py`, `server.py`.

**Interfaces:** Produce `sanitize_text(text: str) -> SanitizedText` (`text`, `redacted`, `ambiguous`); `RecordSanitizer.feed(chunk: bytes) -> bytes`, `.finish() -> bytes`; `to_mcp_result(reply: Reply) -> CallToolResult`; `CaptureBudget` with per-source and aggregate counters. Bytes emitted by the stream filter are already safe complete records; incomplete/ambiguous leading and trailing blocks are withheld.

- [ ] **Write tests:** Parametrize fake PEM blocks, token formats, credential URLs, JSON/YAML/assignment secret keys, authorization/cookie values, chunk splits at every key boundary, malformed UTF-8, oversized lines and a tail starting inside a PEM block. Assert fake values do not appear in serialized text, structured content, warnings, errors, or stderr. A 65,536-byte complete MCP result passes; a result one byte larger is reduced and flagged without invalid JSON or split secrets. Include escaping and duplicated text+structured payload in the count.

Core assertion for the named test (create its test-only fixtures in this task):

```python
def test_secret_assignment_is_redacted():
    result = sanitize_text('password=fake-secret-123')
    assert result.redacted is True
    assert 'fake-secret-123' not in result.text
```

- [ ] **Run red:** `uv run --frozen pytest tests/unit/test_redaction.py tests/unit/test_results.py -q`; expect missing filtering/budget behavior.
- [ ] **Implement:** A single central policy for complete bounded text plus stateful record framing. Preserve source line numbering when omitting/redacting records. Match supported multiline and secret-key forms before excerpting. Use complete sources in later file/Git tools; for a leading log fragment without enough context, omit with warning. Central serialization sanitizes every string field and trims whole data items/records while retaining schema-valid warning/error metadata. Reserve result space before capture and never echo rejected arguments.
- [ ] **Run green:** Same two modules pass; `uv run --frozen pytest tests/integration/test_stdio.py -q` still passes. Counter tests prove ordinary capture stops at 1 MiB, blobs at 1 MiB each/20 MiB total, and file scanning at 20 MiB.
- [ ] **Commit:** `git add src/devtools_mcp_server tests/unit/test_redaction.py tests/unit/test_results.py` then `git commit -m "feat: enforce secret filtering and bounded tool results"`.

## Task 3: Isolated execution with hard deadlines and owned-child cleanup

**Files:** Create `supervisor.py`, `worker.py`, `execution.py`, `platforms/{__init__,posix,windows}.py`, `tests/unit/test_execution.py`, `tests/integration/test_supervisor.py`, `tests/fixtures/inspection_child.py`; Modify `server.py`, `models.py`, `tools/environment.py`.

**Interfaces:** Produce `Supervisor.call(...)` above, `async Supervisor.aclose() -> None`, `run_command(program: Literal['git','docker'], arguments: tuple[str,...], ctx: InspectionContext, *, capture: Literal['ordinary','git_blob']='ordinary') -> CommandCapture`, and `InspectionContext`. `CommandCapture` exposes return code, bounded bytes, and limit status internally only. Platform helpers expose `start_owned_worker(request: bytes) -> WorkerHandle`, `stop_owned_worker(handle: WorkerHandle) -> None`; `WorkerHandle` contains only processes/resources created by this server.

- [ ] **Write tests:** Add a test-owned `four_blocked_calls` async fixture that yields a Supervisor whose four workers are blocked until fixture teardown. Four blocked workers occupy capacity; a fifth returns `BUSY` immediately. A blocking filesystem fake hits the 10-second receipt deadline; no output after cancellation. A worker spawning a long-lived fake Git child is cancelled and both owned processes disappear; an unrelated process and its socket survive. Assert output floods cannot exceed counters and capacity is reusable. Reject client-supplied executable/subcommand/flags, relative/current-directory executable resolution, and inherited credential/helper variables. Use short injectable clocks/deadlines only in internal test constructors; production options cannot change limits.

Core assertion for the named test (create its test-only fixtures in this task):

```python
@pytest.mark.anyio
async def test_fifth_call_is_busy(four_blocked_calls):
    reply = await four_blocked_calls.call('environment_info', {})
    assert reply.error.code == 'BUSY'
```

- [ ] **Run red:** `uv run --frozen pytest tests/unit/test_execution.py tests/integration/test_supervisor.py -q` fails on missing containment/admission behavior.
- [ ] **Implement:** One short-lived private-IPC worker per accepted call; no queue, snapshots, or accumulating threads. Worker is launched with trusted `.venv` Python in isolated mode; imports installed package code only. Parent validates and bounds IPC, starts its deadline before validation, and reserves cleanup/serialization time within 10 seconds. POSIX uses a new session/process group; Windows uses a kill-on-close Job Object. Worker waits for its request until containment is established, preventing child escape during setup. Only then may it invoke Git/Docker; their descendants inherit containment. Drain stdout/stderr incrementally with separate blob accounting, fixed trusted executable resolution, minimal OS-required environment, no shell, no stdin prompts. Killing the containment unit never targets observed PIDs.
- [ ] **Run green:** Both modules and stdio tests pass. Assert parent close and malformed worker replies clean up resources, redact internal failures, and leave zero owned workers; worker replies are serialized/budgeted before returning to SDK.
- [ ] **Commit:** `git add src/devtools_mcp_server tests/unit/test_execution.py tests/integration/test_supervisor.py tests/fixtures/inspection_child.py` then `git commit -m "feat: contain inspection workers and enforce hard deadlines"`.

## Task 4: Verified POSIX file access and central source exclusions

**Files:** Create `paths.py`, `tests/unit/test_path_policy.py`, `tests/platform/test_posix_paths.py`; Modify `platforms/posix.py`.

**Interfaces:** Produce `PathPolicy(config: StartupConfig)`, `PathPolicy.open_text(path: str, ctx: InspectionContext) -> VerifiedTextFile`, `PathPolicy.iter_entries(directory: str, recursive: bool, ctx: InspectionContext) -> Iterator[VerifiedEntry]`, and `PathPolicy.check_storage(path: str, purpose: Literal['file','git','disk'], ctx: InspectionContext) -> VerifiedLocation`. A verified file exposes bounded `.read_utf8() -> str` and context-manager close; it never reopens by pathname. Entries contain normalized relative path, type, size, timestamp and safe locality metadata; errors use the shared codes.

- [ ] **Write tests:** Deny `.env*`, `.ssh`, cloud auth stores, `.npmrc`, `.pypirc`, `.netrc`, Git credential files, Docker config, kubeconfig and private-key extensions before any content-open spy fires; ordinary `package.json`/`tsconfig.json` succeeds. Symlink to `.env` is denied; a permitted direct file alias remains allowed. Reject FIFO, socket, device, proc/sys interfaces, binary text reads, internal `.git`, NFS/SMB mount fakes, cloud placeholders and unknown filesystem classification. Interleave directory/symlink replacement between validation and open and assert the denied object is never read.

Core assertion for the named test (create its test-only fixtures in this task):

```python
def test_env_file_is_denied(tmp_path, inspection_context):
    path = tmp_path / '.env'
    path.write_text('FAKE_TOKEN=fixture-value')
    with pytest.raises(InspectionError) as caught:
        PathPolicy(StartupConfig()).open_text(str(path), inspection_context)
    assert caught.value.code == 'SECRET_BLOCKED'
```

- [ ] **Run red:** `uv run --frozen pytest tests/unit/test_path_policy.py tests/platform/test_posix_paths.py -q` fails on access/identity assertions.
- [ ] **Implement:** Central case-aware built-in directory/basename/extension deny patterns plus additive operator paths; check requested and resolved ancestry. On Linux/macOS walk from verified directory handles using descriptor-relative opens, no-follow checks and final identity/type/storage verification; safe direct aliases resolve then open through the same verified traversal. Use native mount flags/type APIs through stdlib `ctypes` where psutil cannot establish locality; reject unknown and cloud placeholder cases. Recursion never follows symlink directories or crosses mount boundaries. File size and bounded read are both checked, catching growth while reading. No eager whole-disk scan.
- [ ] **Run green:** Same tests pass on native macOS/Linux. FIFO and socket cases return without opening/blocking; permission failures remain explicit. Descriptor leak assertions pass across repeated denied reads.
- [ ] **Commit:** `git add src/devtools_mcp_server/paths.py src/devtools_mcp_server/platforms/posix.py tests/unit/test_path_policy.py tests/platform/test_posix_paths.py` then `git commit -m "feat: verify local POSIX file handles and block secret sources"`.

## Task 5: Native Windows path and storage verification

**Files:** Modify `paths.py`, `platforms/windows.py`; Create `tests/platform/test_windows_paths.py`.

**Interfaces:** Implement the identical `PathPolicy` contracts from Task 4 using Windows handles. Backend selection uses `os.name`, not shell commands or path-string heuristics alone; POSIX imports must not require Windows DLLs and vice versa.

- [ ] **Write tests:** Parametrize case-insensitive secret aliases, drive paths, junctions, reparse-point races, alternate data streams, UNC, remote named paths, mapped SMB drives, unknown locality, cloud recall/offline flags, devices and a valid materialized local file. Assert blocked cases never call a content read or placeholder hydration API. A junction swap after the first check cannot expose fake secret content. Include non-ASCII/space filenames and permission denial.

Core assertion for the named test (create its test-only fixtures in this task):

```python
def test_unc_file_is_denied(inspection_context):
    with pytest.raises(InspectionError):
        PathPolicy(StartupConfig()).open_text(r'\\server\share\source.py', inspection_context)
```

- [ ] **Run red:** On native Windows, `uv run --frozen pytest tests/platform/test_windows_paths.py -q` fails for missing verified-handle behavior; other OSs must report this module as platform-skipped, never as Windows validation.
- [ ] **Implement:** Use `ctypes` Win32 APIs for opened-handle type, final target/identity, volume/drive locality, reparse/recall attributes, and verified ancestry. Do not read a target until identity/locality/policy checks pass. Deny unresolved/unsupported filesystems; support ordinary native local files with the same direct-alias and traversal semantics as POSIX. Keep all handles context-managed.
- [ ] **Run green:** Native Windows tests and shared path-policy tests pass; run Task 3 containment tests on Windows too. Do not call Windows supported solely because mocked tests pass on macOS.
- [ ] **Commit:** `git add src/devtools_mcp_server/paths.py src/devtools_mcp_server/platforms/windows.py tests/platform/test_windows_paths.py` then `git commit -m "feat: verify native Windows files and storage locality"`.

## Task 6: Project list, read and literal search tools with bounded cursors

**Files:** Create `tools/files.py`, `pagination.py`, `tests/unit/test_files.py`, `tests/unit/test_pagination.py`; Modify `tools/__init__.py`.

**Interfaces:** Produce `list_files(args: FilesListArgs, ctx) -> Reply[FilesListData]`, `read_file(args: FilesReadArgs, ctx) -> Reply[FilesReadData]`, `search_files(args: FilesSearchArgs, ctx) -> Reply[FilesSearchData]`; each Args/Data is a strict named model in `tools/files.py`. `FilesReadData.lines` contains `{line: int, text: str}` entries; list and search data use `entries` and `matches` arrays respectively. Produce `encode_cursor(tool: str, filters: dict, position: JsonValue) -> str` and `decode_cursor(value: str, tool: str, filters: dict) -> JsonValue`; reject malformed/oversized cursors (>4,096 characters) and mismatched filters, no stateful snapshots. Cursor positions are untrusted and revalidated, not authorization tokens.

- [ ] **Write tests:** Assert defaults (list nonrecursive, search recursive, 100/200 items, read 1-based 200/1,000 lines); literal case-sensitive search treats `.*` literally. UTF-8 BOM works, invalid encodings/binary return `UNSUPPORTED`, read past EOF returns empty success, >1 MiB direct reads fail and searches omit with partial warning. A PEM interior line and search inside a secret never expose its body. Test traversal skip directories, glob confinement, 10,000-candidate and 20 MiB caps, secret-path omission and mutable pages with mismatched/corrupt cursors.

Core assertion for the named test (create its test-only fixtures in this task):

```python
def test_read_beyond_eof_is_empty(tmp_path, inspection_context):
    path = tmp_path / 'app.py'
    path.write_text('print(1)\n', encoding='utf-8')
    reply = read_file(FilesReadArgs(path=str(path), start_line=9, max_lines=1), inspection_context)
    assert reply.status == 'ok'
    assert reply.data.lines == []
```

- [ ] **Run red:** `uv run --frozen pytest tests/unit/test_files.py tests/unit/test_pagination.py -q` fails on missing tool functions.
- [ ] **Implement:** Consume verified handles/entries only. Sanitize complete bounded sources before line selection/search. Match relative-path globs without filesystem expansion; skip `.git`, `node_modules`, `.venv`, `venv`, `__pycache__`, `dist`, `build`, `target` by default during recursion. Respect per-call entry/work bounds even when sorting for pagination; if no safe continuation is available, return partial with no cursor and narrow-query guidance. Results include only safe relative paths/line numbers and sanitized text. Register the exact public names `files_list` → `list_files`, `files_read` → `read_file`, and `files_search` → `search_files`.
- [ ] **Run green:** Unit suites plus stdio discovery pass; exactly four implemented tools are now exposed. Byte/line limits and source line numbers remain correct after redaction and serialization.
- [ ] **Commit:** `git add src/devtools_mcp_server tests/unit/test_files.py tests/unit/test_pagination.py` then `git commit -m "feat: add bounded project file inspection tools"`.

## Task 7: Processes, sockets, system metrics and capability reporting

**Files:** Create `tools/processes.py`, `tools/system.py`, `tests/unit/test_processes.py`, `tests/unit/test_system.py`, `tests/platform/test_system_smoke.py`; Modify `tools/environment.py`, `tools/__init__.py`.

**Interfaces:** Produce `list_processes(args: ProcessesListArgs, ctx) -> Reply[ProcessesData]`, `list_ports(args: PortsListArgs, ctx) -> Reply[PortsData]`, `system_stats(args: SystemStatsArgs, ctx) -> Reply[SystemData]`; complete `inspect_environment(...)`. `ProcessesData.processes` and `PortsData.sockets` contain the typed rows; socket rows use lowercase `protocol` (`tcp`/`udp`) and `state` (`listen`/`bound`). Model process identities as PID+creation time; memory quantities are integer bytes; UTC timestamps include timezone. `disk_path` defaults to the current local volume; verify with `PathPolicy.check_storage(..., 'disk', ...)` before reading usage.

- [ ] **Write tests:** Add a `fake_system_snapshot` fixture that patches psutil CPU to 25.0%, memory total to 8,192 bytes and available to 4,096 bytes, and uses a verified local test volume for disk queries. Fake psutil snapshots include process exit, PID reuse, inaccessible process/socket owner, zero results versus partial access, TCP listen versus established connection, UDP bindings, IPv4/IPv6, filtered PIDs and changing cursor pages. Assert one shared 0.1-second sampling interval per batch; system CPU stays normalized 0–100 and process CPU may exceed 100. No call to `cmdline()`/`environ()` and no environment enumeration. Disk path on a remote/unknown volume is denied. Missing Git/Docker is a reported capability, not a startup failure.

Core assertion for the named test (create its test-only fixtures in this task):

```python
def test_system_metrics_have_named_units(fake_system_snapshot, inspection_context):
    reply = system_stats(SystemStatsArgs(), inspection_context)
    assert reply.data.cpu_percent == 25.0
    assert reply.data.memory_total_bytes == 8192
    assert reply.data.memory_available_bytes == 4096
```

- [ ] **Run red:** `uv run --frozen pytest tests/unit/test_processes.py tests/unit/test_system.py tests/platform/test_system_smoke.py -q` fails on missing inspectors.
- [ ] **Implement:** Use psutil with selected attributes and per-row permission/exit handling; no broad process dump. Numeric cursors/filters use Task 6 contracts, with explicit partial visibility. System data fields: `cpu_percent`, `cpu_sample_seconds`, `memory_total_bytes`, `memory_available_bytes`, `memory_used_bytes`, `swap_total_bytes`, `swap_used_bytes`, `disk_total_bytes`, `disk_used_bytes`, `disk_free_bytes`, `disk_path`; nullable unobservable values carry warnings. Capabilities report availability/probe evidence and fixed active limits without claiming Docker daemon readiness from executable existence. Register `processes_list` → `list_processes`, `ports_list` → `list_ports`, and `system_stats` → `system_stats`.
- [ ] **Run green:** Unit tests pass, native smoke observes this test process and a test-owned loopback listener on each platform; 7 tools are discoverable. Record macOS connection visibility restrictions explicitly rather than returning an apparently complete empty socket list.
- [ ] **Commit:** `git add src/devtools_mcp_server tests/unit/test_processes.py tests/unit/test_system.py tests/platform/test_system_smoke.py` then `git commit -m "feat: inspect processes ports and system resources"`.

## Task 8: Hardened local Git metadata inspection

**Files:** Create `tools/git.py`, `tests/unit/test_git_execution.py`, `tests/integration/test_git_metadata.py`; Modify `execution.py`, `tools/__init__.py`.

**Interfaces:** Produce `git_status(args: GitStatusArgs, ctx) -> Reply[GitStatusData]`, `git_branches(args: GitBranchesArgs, ctx) -> Reply[GitBranchesData]`, `git_log(args: GitLogArgs, ctx) -> Reply[GitLogData]`, `resolve_commit(repository: str, revision: str, ctx) -> str`. `GitLogData.commits` is the bounded typed commit array. Repository validation checks worktree and actual Git storage locality, including linked-worktree metadata. Git-internal metadata access is private to this adapter; file tools still deny it.

- [ ] **Write tests:** Create an `unborn_repository` fixture returning a freshly initialized temporary repo path with no commits. Build disposable SHA-1 and SHA-256 repos with unborn branch, linked worktree, newline/tab/space/non-ASCII filenames, a dirty index and untracked/ignored files. Assert status semantics, local-only branches and bounded commit subjects without patches. Reject revision expressions, ranges, `:blob`, flag-like values and magic pathspecs; accept HEAD/local names/full IDs in either object format. Malicious fsmonitor/external-diff/textconv/pager/signature settings must not create a sentinel; a partial clone's missing object must not invoke a network fake.

Core assertion for the named test (create its test-only fixtures in this task):

```python
def test_unborn_log_is_empty(unborn_repository, inspection_context):
    reply = git_log(GitLogArgs(repository=str(unborn_repository)), inspection_context)
    assert reply.status == 'ok'
    assert reply.data.commits == []
```

- [ ] **Run red:** `uv run --frozen pytest tests/unit/test_git_execution.py tests/integration/test_git_metadata.py -q` fails on missing adapter or unsafe options.
- [ ] **Implement:** Fixed real Git builtins with NUL-delimited machine-readable formats, no aliases, paging/color, external diff/textconv, signature helpers, fsmonitor/hooks, lazy fetch, prompts or optional index locking. Build a minimal child environment that omits inherited Git overrides; set explicit safe per-command configuration and `GIT_OPTIONAL_LOCKS=0`, `GIT_TERMINAL_PROMPT=0`, `GIT_NO_LAZY_FETCH=1`. Verify installed Git supports required controls; unsupported versions return scoped `UNSUPPORTED`. Resolve permitted refs with end-of-options protection and literal paths after `--`; all Git sources are local. Read .git pointers/metadata only through adapter-specific verified-local access, never generic file read. Sanitize metadata and suppress denied path rows. Register three tools.
- [ ] **Run green:** Both suites plus stdio tests pass; 10 tools discoverable. Compare index/worktree and sentinel state before/after calls; no metadata tool launches a credential/network/helper program.
- [ ] **Commit:** `git add src/devtools_mcp_server tests/unit/test_git_execution.py tests/integration/test_git_metadata.py` then `git commit -m "feat: add hardened local Git status branches and history"`.

## Task 9: Git diffs with secret-path provenance

**Files:** Modify `tools/git.py`; Create `tests/integration/test_git_diff.py`.

**Interfaces:** Produce `git_diff(args: GitDiffArgs, ctx) -> Reply[GitDiffData]`. Args: absolute repository; `mode: Literal['working','staged','commit']`; optional permitted revision only in commit mode (required there); optional ≤100 literal repository-relative paths. Data: bounded list of file changes with old/new safe paths and sanitized patch text. Working is index→worktree, staged is HEAD→index, commit is first-parent→commit; root/unborn use the repository's own empty tree object identity, without writing objects.

- [ ] **Write tests:** Assert working/staged/commit separation, root and merge first-parent semantics and unborn staged diff. Create a `secret_rename_repo` fixture that returns the absolute temporary repo path after staging a rename with the fake value below. Stage `.env`→`settings.txt` containing `FOO=unrecognized-secret`, request only `settings.txt`, and assert no value/patch is emitted; include exact rename identity and recognized modified rename. A private key starts outside the visible hunk; no interior leaks. Binary, symlink, oversized blobs, missing local objects and capped metadata return omissions/partial evidence, never raw fallback diffs. Test both object formats and newline paths.

Core assertion for the named test (create its test-only fixtures in this task):

```python
def test_destination_filter_cannot_expose_env_rename(secret_rename_repo, inspection_context):
    args = GitDiffArgs(repository=str(secret_rename_repo), mode='staged', paths=['settings.txt'])
    reply = git_diff(args, inspection_context)
    assert 'unrecognized-secret' not in reply.model_dump_json()
    assert reply.omitted_count >= 1
```

- [ ] **Run red:** `uv run --frozen pytest tests/integration/test_git_diff.py -q` fails on missing or unsafe patch behavior.
- [ ] **Implement:** Obtain bounded comparison metadata and old/new blob identities before caller path filtering. Deny pairs when either path is protected; preserve exact-rename identity. Verify and sanitize each complete source within blob budgets before rendering patches; construct diff from sanitized line sequences so secret hunk context cannot leak. Worktree sources use verified file handles. Preserve source line numbering; no external diff/textconv or Git binary patch. When required provenance or source is unavailable within limits, omit that change with a reason. Register `git_diff`.
- [ ] **Run green:** Git suites pass, 11 tools discoverable, and read-only state/sentinel assertions still hold. No claim of arbitrary historical secret provenance beyond the spec's stated scope.
- [ ] **Commit:** `git add src/devtools_mcp_server/tools/git.py tests/integration/test_git_diff.py` then `git commit -m "feat: inspect Git diffs without exposing protected sources"`.

## Task 10: Local Docker state and bounded logs

**Files:** Create `tools/docker.py`, `tests/unit/test_docker.py`, `tests/integration/test_docker_local.py`; Modify `tools/__init__.py`, `execution.py`.

**Interfaces:** Produce `select_local_endpoint(config: StartupConfig) -> str | None`, `docker_containers(args: DockerContainersArgs, ctx) -> Reply[DockerContainersData]`, `docker_inspect(args: DockerInspectArgs, ctx) -> Reply[DockerInspectData]`, `docker_logs(args: DockerLogsArgs, ctx) -> Reply[DockerLogsData]`. Container IDs accept validated hex IDs/prefixes, not names or leading flags; state selection is explicit, not full raw inspect. `since` is a timezone-aware ISO timestamp; `tail` defaults 200, max 1,000; never follow.

- [ ] **Write tests:** Reject TCP (even loopback), SSH, remote pipes, inherited context/host/custom-header variables and ambiguous local endpoints. A fake user Docker config contains a fake credential; assert it is never opened, forwarded or returned. CLI always gets the explicit local endpoint and isolated empty config. Selected state excludes env/labels/command/health log contents. Multiline secrets split across log records, truncated leading fragments and stderr never leak. Missing CLI, denied socket and stopped daemon affect only Docker calls.

Core assertion for the named test (create its test-only fixtures in this task):

```python
def test_loopback_tcp_docker_endpoint_is_rejected():
    with pytest.raises(InspectionError):
        select_local_endpoint(StartupConfig(docker_endpoint='tcp://127.0.0.1:2375'))
```

- [ ] **Run red:** `uv run --frozen pytest tests/unit/test_docker.py -q` fails on missing adapter/policy behavior.
- [ ] **Implement:** Accept local Unix sockets or local-machine named pipes only; check socket-path ancestry against remote storage, using a dedicated endpoint check rather than file-content opening. Probe documented Engine/Desktop paths only if none supplied, reject ambiguous discovery, and keep it fixed for the server session. Use fixed Docker builtins and selected format templates; avoid fetching/serializing a raw inspect dump. Create/close a credential-free server-owned temp config, sanitize output before release and apply Task 2 record filtering to logs. Register all three tools.
- [ ] **Run green:** Unit tests pass; opt-in `uv run --frozen pytest tests/integration/test_docker_local.py -m docker -q` inspects a test-created disposable container on each available native target. Container start/stop belong only to test setup/teardown, never the server interface. Exactly 14 tools are now exposed.
- [ ] **Commit:** `git add src/devtools_mcp_server tests/unit/test_docker.py tests/integration/test_docker_local.py` then `git commit -m "feat: inspect local Docker containers and sanitized logs"`.

## Task 11: Loopback-only HTTP health checks

**Files:** Create `tools/http.py`, `tests/unit/test_http_policy.py`, `tests/integration/test_http_local.py`; Modify `tools/__init__.py`.

**Interfaces:** Produce `validate_health_url(url: str, config: StartupConfig) -> ValidatedHealthTarget`, `http_check(args: HttpCheckArgs, ctx) -> Reply[HttpCheckData]`. Args default to HEAD/body false; body true requires GET. Target retains original TLS hostname and pinned loopback address. Data: `http_status: int`, duration seconds, allowed headers and optional sanitized UTF-8 body. Transport failures use `UNAVAILABLE`, `ACCESS_DENIED` or `TIMEOUT` as appropriate, with sanitized causes in message, not raw URL credentials.

- [ ] **Write tests:** Allow exact localhost, canonical 127/8 and `::1` with approved paths; reject other hostnames, DNS tricks, numeric aliases, mapped IPv6, zones, userinfo, fragments, queries, encoded path ambiguity, remote hosts and non-HTTP schemes before network calls. DNS and proxy fakes must never run. Redirect response is returned without following. Test 5-second timeout, TLS verification failure, 404/500 as successful evidence, compressed bodies, >16 KiB headers, >1 MiB body, invalid UTF-8/binary and cookie/authorization exclusion.

Core assertion for the named test (create its test-only fixtures in this task):

```python
def test_remote_http_host_is_rejected_before_connect():
    with pytest.raises(InspectionError):
        validate_health_url('http://example.com/health', StartupConfig())
```

- [ ] **Run red:** `uv run --frozen pytest tests/unit/test_http_policy.py tests/integration/test_http_local.py -q` fails on missing validation/transport rules.
- [ ] **Implement:** Canonical parse and path comparison with default `/`, `/health`, `/healthz`, `/ready`, `/readyz`, `/live`, `/livez`, `/status` plus operator additions. Map localhost directly to 127.0.0.1; connect to a literal loopback address while preserving HTTPS SNI/certificate hostname. Use standard-library `http.client` with an explicit numeric-address socket connection and `ssl.create_default_context()`; set TLS `server_hostname` to the original validated host. Override only connection creation and wrap the response socket's file reader with a byte-counting header reader that stops at 16 KiB before HTTP parsing can buffer more. Do not use `socket.create_connection`/`getaddrinfo`, environment proxies, cookie/auth handlers or redirect helpers. Request identity encoding, reject encoded responses and cap raw body/framing reads at 1 MiB. This standard-library choice replaces the spec's proposed httpx dependency to avoid private transport hooks. Stream raw bytes without decompression, apply content-type/UTF-8 policy and complete bounded-body redaction. Return only content-type/content-length/allow. Register final tool.
- [ ] **Run green:** Both suites pass against test-owned loopback HTTP/TLS fixtures; no external network dependency. Discovery now exposes exactly the spec's 15 names with strict input and output schemas.
- [ ] **Commit:** `git add src/devtools_mcp_server tests/unit/test_http_policy.py tests/integration/test_http_local.py` then `git commit -m "feat: add bounded loopback HTTP health inspection"`.

## Task 12: Whole-server protocol and safety regression checks

**Files:** Create `tests/integration/test_tool_contracts.py`, `tests/integration/test_safety_workflow.py`; Modify `tests/integration/test_stdio.py`, `tests/integration/test_supervisor.py` as needed for final registration.

**Interfaces:** Consume only shipped MCP discovery/call interfaces and fixtures; no new production feature. Each registered output schema accepts its ok/partial/error payload. Error replies use `isError=true`; partial and HTTP 4xx/5xx evidence use `false`. Cancellation remains protocol cancellation, not a forged ordinary result.

- [ ] **Write tests:** Parametrize all 15 names through real stdio: strict input failures, valid structured/text result, dependency absence, denied source, output budget and cancellation. Assert unknown tool is a protocol/dispatch error, stdout has only protocol frames, and no fake secret appears anywhere in any channel. Run four slow tools plus a fifth; check BUSY and cleanup. Compare app-owned PID/socket, index/worktree, container state and fixture contents before/after. Exercise malicious instructions in file/log text as inert data.

Core assertion for the named test (create its test-only fixtures in this task):

```python
@pytest.mark.anyio
async def test_unknown_argument_is_a_tool_error(mcp_client):
    reply = await mcp_client.call_tool('environment_info', {'extra': 'fake-secret-123'})
    assert reply.isError is True
    assert reply.structuredContent['error']['code'] == 'INVALID_ARGUMENT'
    assert 'fake-secret-123' not in reply.model_dump_json()
```

- [ ] **Run red:** `uv run --frozen pytest tests/integration/test_tool_contracts.py tests/integration/test_safety_workflow.py -q`; any missing integration requirement must fail before fixes. Do not create artificial failures if existing behavior already meets the contract; show green and proceed.
- [ ] **Implement fixes:** Repair integration defects in the owning module and its regression tests; no new wrapper/policy system. Verify output schema composition, cancellation propagation, bounded private IPC and worker lifetime all match the spec. Shut down persistent resources on normal exit, errors and parent interruption.
- [ ] **Run green:** `uv run --frozen pytest tests/unit tests/integration -m 'not docker' -q` and `uv run --frozen ruff check src tests` pass. Run Docker opt-in suite separately when available; report skips honestly.
- [ ] **Commit:** `git add src/devtools_mcp_server tests` then `git commit -m "test: verify complete MCP tool and safety contracts"`.

## Task 13: Native OS acceptance and local client handoff

**Files:** Create `README.md`, `docs/tools.md`, `docs/security.md`, `docs/troubleshooting.md`, `docs/validation.md`, `tests/integration/test_api_outage.py`; Modify native platform tests only if acceptance reveals an actual gap. Do not add a publishing or hosted deployment pipeline.

**Interfaces:** Exact launch command is `<absolute checkout>/.venv/bin/python -I -m devtools_mcp_server` on POSIX and `<absolute checkout>\\.venv\\Scripts\\python.exe -I -m devtools_mcp_server` on Windows; editable install or equivalent local package installation makes launch independent of cwd. Client configuration examples preserve Windows JSON escaping and argument arrays, including `-I` to prevent cwd/PYTHONPATH package shadowing. No credentials or environment-file loading in sample configuration.

- [ ] **Write tests:** Create an `unhealthy_api_client` fixture yielding an SDK client and a test-owned loopback `/health` URL returning 503. Fixture API scenarios assert the observable distinctions between no listener, stopped container, unhealthy HTTP status and permission-limited evidence. A smoke test launches the installed package from an unrelated working directory and a checkout path with spaces, then a hostile working directory/PYTHONPATH containing a shadow package with a sentinel side effect; `-I` must prevent its execution. Package imports must still resolve only trusted installed sources. Record actual agent conclusions separately from deterministic evidence tests: the server does not infer diagnosis itself.

Core assertion for the named test (create its test-only fixtures in this task):

```python
@pytest.mark.anyio
async def test_unhealthy_api_status_is_observed_evidence(unhealthy_api_client):
    # Fixture yields (MCP client, test-owned loopback /health URL returning 503).
    client, url = unhealthy_api_client
    reply = await client.call_tool('http_check', {'url': url})
    assert reply.isError is False
    assert reply.structuredContent['data']['http_status'] == 503
```

- [ ] **Run red:** `uv run --frozen pytest tests/integration/test_api_outage.py -q`; expect any missing workflow assertions to identify their owning domain before changes.
- [ ] **Run green and complete native acceptance:** On native Windows x86-64, macOS arm64 and Linux x86-64 run `uv sync --frozen`, then `uv run --frozen pytest tests/unit tests/platform tests/integration -m 'not docker' -q`, `uv run --frozen pytest tests/integration/test_docker_local.py -m docker -q` where Docker is provisioned, and `uv run --frozen ruff check src tests`. Document OS/Python/dependency versions, commands, pass/fail/skips and real permission limits. Use available local or explicitly authorized native runners; if a target is unavailable, leave its acceptance unchecked and report the gap instead of claiming cross-platform completion.
- [ ] **Document and verify:** README explains local environment setup from the lockfile (not machine Python installation), the direct stdio launch, operator options and optional Git/Docker dependencies. Tool reference lists exact schemas and limits. Security doc states denied sources, redaction limits, supported filesystem behavior, HTTP side effects and absence of OS sandboxing. Walkthrough shows an agent composing evidence. Manually connect one intended coding client and record the client/version/launch result in `docs/validation.md`; other clients remain explicitly unverified until exercised. Never infer a client smoke test from unit tests.
- [ ] **Commit:** `git add README.md docs/tools.md docs/security.md docs/troubleshooting.md docs/validation.md tests/integration/test_api_outage.py tests/platform` and any owned bugfix files, then `git commit -m "docs: validate native platforms and document local troubleshooting"`.

## Coverage and execution order

Tasks are sequential because the security and typed interfaces are shared. Domain work could be reviewed independently after Task 6, but a single ordered plan is appropriate: the deliverable is one server with a common safety boundary, not several separately deployable projects.

| Spec requirement | Owning tasks |
| --- | --- |
| Python 3.14, local lockfile, stdio, strict schemas | 1, 12–13 |
| Secret-source denial and content filtering on every output channel | 2, 4–6, 8–12 |
| Four-call capacity, hard deadlines, capture/result ceilings, owned-child cleanup | 2–3, 12 |
| Windows/macOS/Linux handles, local storage, cloud/pseudo-file rejection | 3–5, 13 |
| List/read/search, range/encoding/globs, stateless pagination | 4–6 |
| Process/port/system observations, CPU sampling/PID reuse, partial permissions | 7, 13 |
| Safe Git metadata, object formats, first-parent/root/unborn diffs, secret rename pairs | 8–9 |
| Local Docker endpoint, empty CLI config, safe selected state and log tails | 10, 13 |
| Local HTTP validation, TLS/SNI, byte/header/encoding limits | 11 |
| Complete protocol/error/cancellation behavior and no intentional mutation | 12 |
| Three native target checks, real client smoke and API-outage example | 13 |

## Self-review and completion criteria

Before handing this plan over, verify every spec section maps to the table above; each step has named files, contracts, red/green checks and a commit; interface names/types remain consistent; and each Review Focus case has an owning test. No product code or dependency installation belongs to this planning stage.

During execution, mark a task complete only after its stated checks pass. A skipped native runner, Docker adapter, client smoke or unsafe platform fallback is a remaining acceptance gap, not an implicit scope reduction. Resolve such gaps or report them precisely. Commit locally; pushing, publishing, and changing MCP client settings are separate actions unless the user authorizes them.

## SDK references checked while planning

- [Official Python SDK tools](https://py.sdk.modelcontextprotocol.io/servers/tools/)
- [Official structured result guidance](https://py.sdk.modelcontextprotocol.io/servers/structured-output/)
- [Official SDK test guidance](https://py.sdk.modelcontextprotocol.io/get-started/testing/)
- [Official low-level Server API source](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/lowlevel/server.py): use the pinned release's constructor-based handlers, not stale decorator examples.
