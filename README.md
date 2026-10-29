# DevTools MCP Server

A local troubleshooting toolkit for MCP-compatible coding agents. Inspect files, Git state, processes, ports, system resources, Docker state/logs, and loopback health endpoints through 15 typed tools.

Python 3.14 · stdio · Windows, macOS, Linux · inspection only

The server returns observations; your agent interprets them. Known secret sources are blocked before content reads, and recognized secrets in permitted text are redacted. This is not a guarantee that arbitrary text is secret-free. Read [the security boundary](docs/security.md) before connecting it to an agent.

## Local setup

Prerequisites: standard CPython 3.14 and `uv`. Optional: Git 2.45+ for Git tools, and a running local Docker engine with its CLI for Docker tools. Missing optional dependencies do not disable unrelated tools.

From your checkout:

```sh
uv sync --frozen --no-python-downloads
uv run --frozen pytest -m 'not docker' -q
uv run --frozen ruff check src tests
```

Dependencies come from `uv.lock`; this installs the checkout locally. There is no npm package, hosted endpoint, publishing step, or runtime dependency download.

## Connect a client

Use your checkout's **absolute virtual environment Python path**, with `-I`. Replace the example paths with your own. The process reads MCP from stdin and writes MCP to stdout; running it alone normally waits for a client.

For clients supporting an `mcpServers` JSON configuration, on macOS/Linux:

```json
{
  "mcpServers": {
    "devtools": {
      "command": "/absolute/path/devtools-mcp-server/.venv/bin/python",
      "args": ["-I", "-m", "devtools_mcp_server"]
    }
  }
}
```

Windows:

```json
{
  "mcpServers": {
    "devtools": {
      "command": "C:\\Dev\\devtools-mcp-server\\.venv\\Scripts\\python.exe",
      "args": ["-I", "-m", "devtools_mcp_server"]
    }
  }
}
```

Keep the executable and arguments separate, including for paths containing spaces. `-I` prevents working-directory and `PYTHONPATH` package shadowing. Client configuration formats vary; actual exercised clients are listed in [validation](docs/validation.md).

Optional startup arguments, appended after the module name:

- `--exclude-secret-path /absolute/private/location` adds a file or directory exclusion; repeat as needed.
- `--allow-health-path /internal-health` adds an exact health URL path; repeat as needed.
- `--docker-endpoint unix:///absolute/docker.sock` selects one local endpoint. Windows example: `npipe:////./pipe/docker_engine`.

Restart the server after changing options or optional executable availability. Docker endpoint selection is frozen for the session. The server does not load `.env` files or Docker credential/config files.

## Try it

Ask your agent: “Why isn't my API responding on port 8000? Check listeners and `/health`, then inspect relevant Git changes and container state. Explain what the evidence proves and what is uncertain.”

[Tools and limits](docs/tools.md) · [Security](docs/security.md) · [Troubleshooting walkthrough](docs/troubleshooting.md) · [Validation evidence](docs/validation.md) · [Contributing](CONTRIBUTING.md)

Work flows through topic branches and PRs into `dev`. `main` is the stable branch; merging and releases are separate owner actions.
