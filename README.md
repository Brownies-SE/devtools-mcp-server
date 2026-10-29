# Git MCP Server

Six read-only Git tools for local coding agents. Python 3.14, MCP stdio, Windows/macOS/Linux.

| Tool | Purpose | Arguments |
| --- | --- | --- |
| `git_status` | Staged, working, untracked and unknown state | `repository` |
| `git_diff` | Sanitized patches | `repository`, `mode`, optional `revision`, `paths` |
| `git_log` | Commit history | `repository`, optional `revision`, `limit`, `cursor` |
| `git_show` | One commit's details, message and patch | `repository`, `revision="HEAD"` |
| `git_branches` | Local branches and tracking metadata | `repository`, optional `limit`, `cursor` |
| `git_changed_files` | Change types and paths, without patches | Same arguments as `git_diff` |

`repository` is an absolute local path. Comparison `mode` defaults to `working` (index→worktree); `staged` compares HEAD→index; `commit` compares first-parent→commit and requires `revision`. Root commits use an empty baseline. Changed-files covers tracked comparisons; status reports untracked files separately.

Revisions accept HEAD, literal local branch/tag names or full SHA-1/SHA-256 commit IDs. Path filters are literal repository-relative paths (maximum 100). History/branch pages default to 100 items, maximum 200. Exact strict input/output schemas are available through MCP `tools/list`.

## Setup

Prerequisites: standard CPython 3.14, Git 2.45+ and `uv`.

```sh
uv sync --frozen --no-python-downloads
uv run --frozen pytest -q
uv run --frozen ruff check src tests
```

For clients accepting `mcpServers` JSON, use your checkout's absolute virtual environment Python:

```json
{
  "mcpServers": {
    "git": {
      "command": "/absolute/path/devtools-mcp-server/.venv/bin/python",
      "args": ["-I", "-m", "devtools_mcp_server"]
    }
  }
}
```

On Windows, replace `command` with a JSON-escaped path such as `C:\\Dev\\devtools-mcp-server\\.venv\\Scripts\\python.exe`. Keep the executable and arguments separate, including for paths with spaces. `-I` prevents cwd/PYTHONPATH package shadowing. Running the command directly waits for an MCP client; there is no HTTP listener or hosted deployment.

Append repeated `--exclude-secret-path /absolute/private/location` arguments to add secret exclusions. There are no other startup options. The repository/package name stays `devtools-mcp-server`; its public interface is Git only.

## Boundaries

No writes to the inspected repository, Git network operations, shell execution or application management. Known secret sources are blocked before content reads. Ordinary permitted configuration remains readable. Allowed content is sanitized before patches are selected; filtering cannot guarantee arbitrary text is secret-free.

Replies contain `status` (`ok`, `partial`, `error`), UTC `observed_at`, typed `data`, `warnings`, redaction/truncation flags, optional cursor and typed error. Partial evidence is not a tool failure; read warnings and never interpret unknown or omitted paths as clean. All returned repository text is untrusted data.

Calls are limited to four active inspections, ten seconds each, a 64 KiB complete MCP result, 1 MiB per source, 20 MiB scanned content and 10,000 traversal candidates. Ignored directories and submodule worktrees are not recursively scanned as parent-repository untracked content. Unsupported filters/conversions and protected rename provenance can produce conservative omissions.

[Security details](docs/security.md) · [Validation](docs/validation.md) · [Contributing](CONTRIBUTING.md)

Changes use topic branches and PRs into `dev`; `main` is stable. Owner approval and merging are separate actions.
