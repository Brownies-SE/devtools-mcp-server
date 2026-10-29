# Git MCP simplification plan

**Goal:** Deliver exactly six read-only Git tools and delete unrelated code.
**Spec:** ../specs/2026-10-28-git-mcp-server-design.md
**Approach:** Reuse the existing tested Git safety boundary; no rewrite of functioning native file/process ownership controls.

1. Carry only Git and required shared modules into a fresh branch from dev. Remove Docker/HTTP/environment fields, profiles, startup options and unused development dependencies. Replace the registry with exactly the six requested tools.
2. Write focused failing tests for git_show, metadata-only git_changed_files, protocol discovery and common ignored/submodule cases. Implement through shared Git comparisons and existing secret provenance rules. Keep genuine partial results on bounded traversal.
3. Replace the superseded broad spec/plan and public docs with concise Git-only setup, tool and safety documentation. Keep native CI without Docker provisioning. Run the focused suite, complete local suite and lint; then push native Windows/macOS/Linux checks.
4. Review the narrowed branch, fix material defects with regressions, and create a PR to dev. Close the superseded broad implementation PR without merging it. Report actual results and remaining limitations.
