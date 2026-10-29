# Validation record

Recorded 2026-10-28 (America/Denver; hosted runs may display 2026-10-29 UTC). This is execution evidence, not a claim of exhaustive secret detection or filesystem support.

## Local and native checks

Local host: macOS 27.0, arm64, CPython 3.14.10, uv 0.13.6. Lockfile versions: MCP 2.3.0, Pydantic 2.14.1, psutil 7.2.2; development: pytest 9.2.0, Ruff 0.17.3, AnyIO 4.15.1, cryptography 50.0.2, jsonschema 4.26.0.

Commands:

```sh
uv sync --frozen --no-python-downloads
uv run --frozen pytest -m 'not docker' -q --tb=short
uv run --frozen ruff check src tests
uv run --frozen ruff format --check src tests
uv run --frozen pytest tests/integration/test_docker_local.py -m docker -q -rs
```

Safety implementation commit `08cbffa` passed all three native targets in [run 40795671167](https://github.com/Brownies-SE/devtools-mcp-server/actions/runs/40795671167): Windows x86-64, macOS arm64 and Linux x86-64, CPython 3.14.9. Linux and local macOS: 210 passed, 12 Windows-only skips, one Docker deselection. Native Windows includes handle enumeration and kill-on-close job tests. That run also reported one asyncio closed-pipe cleanup warning in a delayed-reaping test; it is retained for final review rather than hidden. The final acceptance test commit is validated by its own PR checks; this record does not substitute an earlier commit's result for later changes.

Real stdio tests exercise all 15 schemas, output budgets, errors, protected sources, concurrency/cancellation, parent termination cleanup, file/Git read-only behavior, process/socket observations, and bounded loopback HTTP including verified temporary TLS. New acceptance scenarios check HTTP 503 as successful observation, no-listener transport failure, and isolated launch through a virtual environment path with spaces from a hostile unrelated cwd/PYTHONPATH. Deterministic permission-denial tests require explicit partial evidence rather than false empty success. Native ordinary-user socket visibility may be partial.

## Actual coding-client smoke

Claude Code **2.1.199**, native macOS: launched interactively with only a temporary command-line `--mcp-config`, `--strict-mcp-config`, empty `--setting-sources` and disabled built-in tools. The configured executable was this checkout's absolute `.venv/bin/python`, with `-I -m devtools_mcp_server`.

The actual `/mcp` panel showed **`devtools · connected · 15 tools`**. This proves real client initialization and discovery. No model prompt was submitted, no real outage diagnosis was inferred, and no persistent MCP configuration was installed. SDK tests separately exercise tool calls. Cursor, Claude Desktop and Codex client integrations remain unverified; the README JSON shape is for clients accepting `mcpServers`.

## Docker and other limits

The optional live container test was attempted locally. A Docker CLI and local socket exist, but `docker version` with an isolated empty config reports that the daemon is not running. The fixture skipped because its cached BusyBox image prerequisite could not be inspected; a public image preparation attempt could not connect either. No application container was changed and the daemon was not started automatically.

Selected-field Docker command construction, credential avoidance, endpoint selection/freezing, log filtering and fake-adapter failures have deterministic coverage. **Live Docker container/state/log acceptance remains unchecked** until an operator provisions the daemon and cached `busybox:stable`, then runs the opt-in command above. macOS/Windows Docker Desktop and Windows named-pipe daemon interoperability are not inferred from mocked tests or general OS CI. The opt-in fixture creates and removes only its own disposable container.

No claim is made about every network filesystem, cloud provider, unusual endpoint policy or unrecognized secret format. The server does not elevate permissions, disable TLS verification, run project applications or repair the observed environment.
