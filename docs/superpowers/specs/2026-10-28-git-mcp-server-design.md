# Local Git MCP server

The user's revised scope replaces the broader DevTools server. Keep Python 3.14 and local MCP stdio, with exactly six read-only tools:

| Tool | Purpose |
| --- | --- |
| git_status | Safe staged, working, untracked and unknown classifications |
| git_diff | Sanitized working, staged or first-parent commit patches |
| git_log | Paginated local commit history |
| git_show | One commit's metadata, full sanitized message, parents and patch |
| git_branches | Paginated local branches and selected tracking metadata |
| git_changed_files | Change types and safe paths without patch bodies |

Reuse the working Git adapter and the shared path, redaction, output-limit and owned-worker protections it needs. Delete Docker, HTTP, general file, process, socket and system tools and their tests/config/dependencies. Keep the existing package/repository name to avoid needless migration.

All tool requests name an absolute repository. Revisions are HEAD, literal local branch/tag names or full SHA-1/SHA-256 commit IDs, never arbitrary revision expressions. Diff and changed-files modes are working, staged or commit (revision required for commit); show defaults to HEAD. Existing literal path filters apply to comparisons. Changed-files reports tracked comparisons; status reports untracked files separately.

No mutations, arbitrary shell, network Git operations, helper/filter execution or credential retrieval. Known secret sources are denied before reads; complete allowed content is sanitized before patch selection. Private Git metadata views avoid loading original executable configuration. Pattern filtering cannot guarantee arbitrary text contains no secrets.

Preserve useful partial evidence when limits or permissions prevent a complete scan. Do not traverse tracked submodule worktrees. Ignored/generated directories must not make common repositories unusable. Maintain native Windows/macOS/Linux checks, four active calls, ten-second deadlines, bounded capture and 64 KiB complete MCP results.

The user explicitly requested immediate simplification and cleanup; no new domain features, service, package publishing or speculative extensibility. Changes go on a topic branch with an unmerged PR to dev.
