# Repository workflow

- `main` is the stable release branch; `dev` is the integration branch.
- Start each change on a new topic branch based on the latest `origin/dev`. Use names such as `feat/...`, `fix/...`, or `chore/...`.
- Do not commit or push changes directly to `main` or `dev`.
- Push the topic branch and open a pull request with an explicit `dev` base as part of delivering requested work.
- Keep pull requests open for review unless the user explicitly asks to merge.
- Promote `dev` to `main` only through a separate release pull request when a release is requested.
- Include the change's purpose, validation results, and known limitations in each PR.
- Never force-push or delete shared branches.

# Project context

- Implement the reviewed specification in `docs/superpowers/specs/2026-10-28-git-mcp-server-design.md`.
- Follow the implementation plan in `docs/superpowers/plans/2026-10-28-git-mcp-server.md`.
- Target standard Python 3.14 on Windows, macOS, and Linux.
- The server is local, uses MCP stdio, exposes exactly six read-only Git tools, and must exclude known secret sources.
- Do not add machine Python installation instructions to the specification.
