# Git-only validation

The six-tool Git server replaces the broader DevTools implementation. Results for the former Docker/HTTP/process tools do not validate this interface.

Local target: macOS arm64, standard CPython 3.14.10, uv 0.13.6. Native CI targets standard CPython 3.14 on Windows x86-64, macOS arm64 and Linux x86-64. Dependencies are frozen in `uv.lock`.

```sh
uv sync --frozen --no-python-downloads
uv run --frozen pytest -q --tb=short
uv run --frozen ruff check src tests
uv run --frozen ruff format --check src tests
```

Focused Git checks cover SHA-1/SHA-256, root/unborn commits, linked worktrees, literal revisions, ignore rules, protected source/rename suppression, filter/include avoidance, safe patches and metadata-only changed-files. Real stdio checks discover exactly six tools, exercise every tool and validate strict errors, output schemas, channel consistency and result limits from a hostile unrelated cwd/PYTHONPATH. Native path and worker tests cover local identity, cancellation, owned-child cleanup and capacity/deadlines.

The Git-only branch's final local/native results and actual coding-client discovery are recorded in its pull request. No claim is made about every filesystem, cloud provider, Git extension or coding client. Pattern filtering is not proof that arbitrary text contains no secrets.

## Recorded results

On 2026-10-28, the final local suite after the focused review fixes passed **165 tests**, with 12 Windows-only skips. Ruff check/format and diff checks passed. The first Git-only native run [40804976651](https://github.com/Brownies-SE/devtools-mcp-server/actions/runs/40804976651) passed all three target OSs at commit `783315f`; the final post-review commit is checked separately on [PR #4](https://github.com/Brownies-SE/devtools-mcp-server/pull/4).

Actual client smoke: Claude Code **2.1.312** connected through a temporary command-line MCP configuration and its `/mcp` panel showed **git · 6 tools**. No model prompt was submitted and no persistent MCP configuration was installed. SDK integration tests separately call every tool. Other coding clients remain unverified.

The independent focused review identified executable-mode, unreadable-file partial-result and untracked-symlink classification defects. Twelve SHA-1/SHA-256 regression cases reproduced those failures, then passed after the fixes. There are no deferred review findings.
