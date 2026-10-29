# Tool reference

All tools publish strict JSON input and output schemas through MCP `tools/list`. Unknown fields and type coercion are rejected. Paths must be absolute unless explicitly described as repository-relative. [tool-schemas.json](tool-schemas.json) contains the exact schema snapshot for this implementation.

Paginated tools accept `limit` (integer, default 100, range 1–200) and optional `cursor` (string, maximum 4,096 characters). Reuse the same filters with the returned cursor. A page is a fresh observation: changing directories/processes/containers can produce omissions or repeats. Git log cursors retain their validated commit anchor.

| Tool | Arguments in addition to pagination where supported | Returned evidence |
| --- | --- | --- |
| `environment_info` | None; no pagination | OS, Python/server versions, dependency capabilities and limits |
| `files_list` | `directory`; optional `glob`; `recursive=false`; paginated | Relative path, type, size and modification time |
| `files_read` | `path`; `start_line=1`; `max_lines=200` (1–1,000); no pagination | Numbered UTF-8 lines after complete-source filtering |
| `files_search` | `directory`, literal `query` (1–1,024 characters); optional `glob`; paginated | Relative paths, line numbers and sanitized matching text; recursive |
| `processes_list` | Optional `name`, `pid`; paginated | PID/parent, name, state, creation time, sampled CPU %, RSS bytes |
| `ports_list` | Optional `port` (1–65,535), `protocol` (`tcp`/`udp`), `pid`; paginated | Visible TCP listeners and UDP bindings, addresses and available owners |
| `system_stats` | Optional `disk_path`; no pagination | Sampled CPU %, memory/swap and local volume byte counts |
| `git_status` | `repository`; no pagination | Branch and safe staged/unstaged/untracked/conflict classifications |
| `git_branches` | `repository`; paginated | Local branches and selected tracking metadata; no fetching |
| `git_log` | `repository`; optional `revision`; paginated | Commit ID, parent IDs, time and bounded subject |
| `git_diff` | `repository`; `mode=working` (`working`/`staged`/`commit`); `revision` required only for commit mode; optional `paths` (at most 100 literal repository-relative paths); no pagination | Safe old/new paths and sanitized patches |
| `docker_containers` | `include_stopped=false`; paginated | ID, name, image, state and published-port summary |
| `docker_inspect` | `container_id` (1–64 hexadecimal characters); no pagination | Running/health status, restart count, ports and mount destinations |
| `docker_logs` | `container_id`; `tail=200` (1–1,000); optional timezone-qualified ISO `since`; no pagination | Bounded sanitized log records; never follows logs |
| `http_check` | `url`; `method=HEAD` (`HEAD`/`GET`); `include_body=false` (true requires GET); no pagination | HTTP status, duration, permitted headers and optional sanitized UTF-8 body |

Paths/URLs have a 4,096-character ceiling; names/revisions have a 256-character ceiling unless the schema specifies otherwise. Search is literal, not regex. Globs match repository/directory-relative names. Git revisions are HEAD, literal local branch/tag names, or full 40/64-character object IDs; expressions, ranges and magic pathspecs are rejected. Working diffs compare index→worktree, staged compares HEAD→index, and commit compares first-parent→commit, including root commits.

HTTP accepts canonical `localhost`, IPv4 loopback and IPv6 `::1` only. Default exact paths: `/`, `/health`, `/healthz`, `/ready`, `/readyz`, `/live`, `/livez`, `/status`. No credentials, query strings, fragments, redirects, DNS, proxies or compression. HTTPS verifies certificates against the original hostname. Returned headers are limited to `content-type`, `content-length`, `allow`. A 503 is valid observed evidence, not a tool failure. Unsupported/incomplete bodies are omitted explicitly.

## Reply contract and bounds

Every structured reply contains `status` (`ok`, `partial`, `error`), UTC `observed_at`, typed nullable `data`, `warnings`, `truncated`, `redacted`, nullable `omitted_count`, `next_cursor`, and `error`. The text channel mirrors that filtered reply. Useful partial evidence has MCP `isError=false`; failed inspections have `isError=true`. A null field means unknown/unavailable, not zero. Read warnings before drawing conclusions.

Error/warning codes: `ACCESS_DENIED`, `SECRET_BLOCKED`, `DEPENDENCY_MISSING`, `UNAVAILABLE`, `UNSUPPORTED`, `INVALID_ARGUMENT`, `NOT_FOUND`, `TIMEOUT`, `OUTPUT_LIMIT`, `BUSY`. Unknown tool names remain protocol dispatch errors. Request cancellation remains MCP cancellation.

- Four active calls, no queue; additional calls return `BUSY`.
- Ten seconds per inspection; HTTP has a five-second total deadline.
- 64 KiB for the serialized MCP result, including text and structured channels.
- 1 MiB per file/blob, 20 MiB total file scan or Git blob content per call, 10,000 traversal candidates.
- Ordinary helper stdout+stderr: 1 MiB total per call; Git blob stderr shares that cap.
- HTTP headers: 16 KiB; body and framing: 1 MiB raw wire bytes.

Limits can reduce results. An output-clipped reply drops its cursor rather than suggesting a complete continuation. There are no tools to edit files, execute shell commands, stop applications, mutate Git or manage containers.
