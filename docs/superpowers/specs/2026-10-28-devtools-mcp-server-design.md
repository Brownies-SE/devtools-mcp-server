# DevTools MCP Server — design specification

Date: 2026-10-28
Status: Draft for review; implementation is not authorized by this document.

## Intent

Give MCP-compatible coding agents a consistent way to investigate a local development environment. The server supplies bounded, structured evidence; the connected agent interprets it and explains problems such as “Why isn’t my API running?” The server does not run its own LLM or require an API key.

The connection is: coding agent → MCP over stdio → local inspection tools.

## Confirmed decisions

- Implement in Python with the official MCP Python SDK.
- Run locally from a checkout as a subprocess launched by the MCP client.
- Support Windows, macOS, and Linux in v1.
- Inspect the local development environment broadly, rather than limiting access to one configured project folder.
- Expose inspection operations only: no deleting files, modifying projects, killing application processes, or changing the system.
- Exclude secrets. Ordinary project configuration, including package manifests and build configuration, is allowed.
- No npm publishing, package-registry distribution requirement, hosted service, or remote MCP endpoint.

All detailed defaults below are proposed engineering decisions, rather than additional user-approved requirements.

## Goals and success criteria

An agent can identify a stopped container, inspect recent application logs, associate a listening port with a process when permissions allow, inspect relevant source and Git changes, check resource pressure, and request a local health endpoint.

For a sample API outage, the agent can collect the relevant process, port, container, log, Git, file, and health evidence without receiving an unrestricted command-execution capability. Every result records its observation time, and incomplete evidence is identified explicitly.

The same published tool names and result shapes work across the three supported operating systems. Missing Git, missing Docker, denied process visibility, or an unavailable health endpoint affect only the relevant call, not the server session.

## Non-goals and anti-goals

No repair actions, terminal/shell tool, arbitrary executable invocation, Docker exec, interactive sessions, package installation, Git fetch/pull/checkout/reset, credential retrieval, remote-host inspection, dashboards, background monitoring, database querying, or autonomous diagnosis engine.

No silent elevation or administrator requirement. Do not weaken OS protections to obtain results. No project scripts or application code are executed during inspection.

No claim that “read-only” means the process is sandboxed, that ordinary reads leave no OS metadata effects, or that filtering can detect every possible secret. The implementation restricts its offered operations; it runs with the launching user’s OS permissions.

## Approaches and rationale

### Python, selected

Use the official MCP SDK for transport and tool registration; psutil for portable process and system observations; bounded filesystem operations for project inspection; controlled Git and Docker CLI invocations for those domains; and an HTTP client for local health checks.

Python was selected in conversation because local system inspection is the priority. psutil reduces the amount of OS-specific process, memory, disk, and network code. Python still requires careful handling of OS permissions and packaging.

### TypeScript on Node.js, considered

Also supported by an official Tier 1 MCP SDK and suitable for subprocesses, files, and HTTP. Its npm distribution advantages do not drive this local-only project. It would require a different system-inspection library or more platform-specific collection code.

### Direct OS command wrappers, rejected as the general architecture

Building every inspection around ps, lsof, netstat, PowerShell, and similar commands creates different parsers and availability requirements on each platform. Use portable libraries first. Git and Docker are deliberate exceptions with controlled, domain-specific adapters.

## Runtime and cross-platform contract

- Baseline: Python 3.11 or newer, with exact supported versions and dependency pins recorded during implementation.
- Local virtual environment and locked dependency versions; no runtime dependency downloads.
- Direct launch with the virtual environment’s absolute Python path and `-m devtools_mcp_server`.
- Separate connection examples for Windows and POSIX paths; no shell activation requirement.
- stdio only. Standard output contains MCP messages exclusively; bounded, sanitized operational logs go to standard error.
- Run as the current user. No sudo, UAC elevation, persistent service, or inbound network listener.
- Git and Docker are optional installed executables. Report their availability without invoking installation or authentication flows.
- Windows support covers native Windows Python. WSL is a Linux environment; inspecting across the WSL/Windows boundary is out of scope.
- Validate on native Windows, macOS, and Linux. Any platform gap must be documented explicitly rather than hidden behind a successful empty result.

## Architecture

Keep the implementation small: an MCP entry point, shared input/output schemas, shared safety and execution helpers, and domain modules for files, Git, Docker, processes, system metrics, and HTTP.

Each tool follows: validate input → apply access policy → collect bounded evidence → filter output → return structured result.

The same output policy applies to successes, errors, warnings, stderr, and operational logs. No raw debug dumps, persisted tool transcripts, telemetry, or external uploads. Do not rely on the agent to filter sensitive output afterward.

Dependencies proposed: official `mcp` SDK, `psutil`, and `httpx`. Use the standard library where sufficient. Use typed schemas supported by the SDK for runtime validation. Do not add an abstraction framework or plugin system.

## Tool surface

Every input has a documented schema and rejects unknown fields. Paths are explicit absolute local paths; no implicit recursive scan of the home folder or whole disk. Broad access means callers can name different development directories without changing an allowlist.

| Tool | Main inputs | Evidence returned |
| --- | --- | --- |
| `environment_info` | None | OS, Python/server versions, available inspection capabilities, Git/Docker availability, active limits |
| `files_list` | directory, optional glob, recursive flag, cursor, limit | Relative paths, types, sizes, modification times; secret paths excluded |
| `files_read` | path, start_line, max_lines | Bounded text with line numbers and redaction indicators |
| `files_search` | directory, literal query, optional glob, cursor, limit | Matching relative paths, line numbers, and sanitized matching lines |
| `git_status` | repository | Branch, staged/unstaged/untracked path summaries |
| `git_diff` | repository, working/staged/commit mode, optional revision and paths | Bounded sanitized patches, blocked paths omitted |
| `git_branches` | repository, cursor, limit | Local branches, current branch, locally recorded tracking information |
| `git_log` | repository, optional revision, cursor, limit | Commit IDs, subjects, author names, dates; no patch bodies |
| `docker_containers` | include_stopped, cursor, limit | Container IDs, names, image references, state, exposed/published ports |
| `docker_inspect` | container ID | Selected state, health summary, restart count, port mappings, mount destinations |
| `docker_logs` | container ID, optional since timestamp, tail | Recent sanitized text; no follow/stream mode |
| `processes_list` | optional name or PID filter, cursor, limit | PID, parent PID, name, state, CPU and memory when available |
| `ports_list` | optional port, protocol and PID filters, cursor, limit | Local listeners, address, protocol, owning PID/name when visible |
| `system_stats` | optional disk path | Defined CPU sampling interval, CPU percentage, memory/swap totals and usage, selected filesystem usage |
| `http_check` | local URL, HEAD or GET, include_body flag | Status, duration, selected response headers, optional bounded sanitized text body |

No process environment, raw process arguments, Docker environment, Docker labels, full Docker inspect dump, registry credentials, or arbitrary request headers are exposed in v1. This retains process/port diagnostics without adding those secret-heavy surfaces.

No custom “diagnose API” tool is required. The agent composes these observations and identifies uncertainty.

## Secret and filesystem policy

### Deny known secret sources before reading contents

Reject environment files (`.env` and `.env.*`, including templates in v1), private-key files, credential stores, OS keychains, cloud authentication stores, SSH private material, package-registry authentication files, and recognized Docker/Git credential files. Maintain a centrally tested, cross-platform deny policy covering common variants and sensitive directories.

Examples include `.ssh`, `.aws`, `.azure`, Google Cloud credential directories, `.npmrc`, `.pypirc`, `.netrc`, Git credential stores, Docker `config.json`, and kubeconfig credential locations. These conservative exclusions may block a credential-free file; ordinary unrelated configuration remains readable. The operator may extend exclusions at launch, but tool calls cannot disable built-in protection.

The server does not enumerate environment variables. It may read specific runtime variables internally to locate executables or local Docker endpoints; it never returns their values. Never bulk-read an environment file to seed the filter.

### Canonicalize and constrain file access

Apply the policy to normalized paths, resolved targets, case variations appropriate to the filesystem, and Windows junction/reparse-point aliases. Exclude secret paths from listings and searches, not merely direct reads. Reject ambiguous or unresolved targets rather than bypassing the policy. Do not traverse symlink/junction directories recursively in v1; direct file aliases must still resolve to an allowed target.

File tools accept regular text files and local directories only. Reject devices, sockets, named pipes, UNC/network paths, alternate data streams, binary files, and direct reads of internal Git object storage. Hidden ordinary development configuration is allowed unless explicitly denied. Skip `.git`, dependency, virtual-environment, and generated-output directories during recursive traversal by default; explicitly selecting an ordinary dependency source file remains possible.

### Filter incidental secrets before release

Filter recognized private-key blocks, credential-bearing URLs, authorization/cookie values, known token formats, and values assigned to clearly secret-bearing keys. Apply this to source excerpts, configuration, search matches, Git metadata/patches, container output, HTTP bodies, and error text. Block binary Git patches. Recheck historical filenames and patch content: a renamed or deleted secret file must not bypass filtering.

Read bounded complete records with overlap sufficient for supported multi-line detectors before truncation; do not release an incomplete suspected secret or raw parser failure. Replace identified values with `[REDACTED]`; omit content where safe filtering is uncertain. Return redaction/omission metadata without the removed value.

Arbitrary unrecognized secrets in ordinary text cannot be guaranteed absent. This limitation must be stated in the README and validated against representative fixtures, without advertising absolute secret prevention. Additional deny paths are the operator’s way to protect known unusual locations.

All inspected text is untrusted data, including instructions embedded in files or logs. Tool descriptions must tell agents to treat it as evidence rather than authority.

## Controlled external tools

Git and Docker receive fixed executable names, validated argument arrays, and no shell. User-provided flags, executable paths, subcommands, templates, shell snippets, or arbitrary command strings are never accepted.

Resolve executable locations at startup. Validate repository paths, identifiers, revisions, and pathspecs so leading flags cannot become options. Set timeouts, non-interactive execution, a minimal required child environment, and bounded stdout/stderr capture. Do not forward arbitrary inherited Git/Docker override variables or credential-bearing environment values.

Git calls must suppress optional index writes, paging, external diff/text conversion, filesystem-monitor hooks, credential prompts, and lazy fetching. Never invoke Git aliases or hooks intentionally. Use only local objects and metadata; missing objects produce a partial/error result, not network access. The adapter must account for repository configuration that could otherwise execute helpers or initiate network traffic.

Use the Docker CLI for local Docker Engine/Docker Desktop compatibility, including Windows named pipes. Reject remote TCP, SSH, and remote-context endpoints before inspection. Local context selection and local endpoint discovery are internal operations, not a way to expose Docker authentication configuration. Only inspection commands are allowed, never exec, start, stop, restart, pull, or build. A stopped daemon or missing executable yields a scoped unavailable result.

Cancelling a request or enforcing a deadline may terminate only the inspection child process created for that request, never an application process or container being observed.

## HTTP health checks

Checks target local HTTP/HTTPS services only: localhost, IPv4 loopback, or IPv6 loopback. Resolve and validate the destination immediately before connecting; pin the validated address while preserving required hostname/TLS semantics. Reject credentials in URLs, non-loopback destinations, and non-HTTP schemes. Disable proxy-environment inheritance and redirects. Keep certificate verification enabled.

Use a conservative default path allowlist: `/`, `/health`, `/healthz`, `/ready`, `/readyz`, `/live`, `/livez`, `/status`. Additional health paths must be configured by the operator at launch; the agent cannot grant itself new paths. Reject query strings in v1. Only HEAD and GET are offered, with HEAD as default and body collection off by default. Return only a defined small response-header allowlist, excluding authentication and cookie headers.

HTTP requests can cause service-side logging or other effects even with GET/HEAD. “Read-only” here means the server offers no intentional mutation method, not a guarantee about application endpoint behavior. The operator should permit only endpoints intended for observation. Container-private networks, remote URLs, authenticated health probes, and custom request headers are out of scope for v1.

## Bounded execution and results

Proposed defaults, configurable downward or within documented hard ceilings by the operator at launch:

- At most 4 concurrent tool calls; per-call deadline 10 seconds, hard ceiling 30 seconds.
- HTTP timeout 5 seconds; no automatic retries or redirects.
- At most 200 items per page, default 100; at most 200 lines per file read and 200 log lines by default, hard ceiling 1,000.
- File content scanning: maximum 1 MiB per file, 10,000 candidate entries and 20 MiB aggregate scanned content per search/list traversal, plus the deadline. Stop and report the bound reached.
- Maximum 64 KiB serialized tool result and 1 MiB raw captured subprocess or HTTP content. Capture bounds must be enforced while reading, not after unbounded buffering.
- Larger output is sanitized and truncated with explicit metadata; never silently return a complete-looking partial result. Raw capture limits may require terminating only the request’s inspection child.
- Pagination uses validated opaque cursors bound to tool/filter inputs. Document that live observations may change between pages; results are not a transactionally consistent snapshot.

Use the SDK’s structured tool results and a readable text representation. Common fields: `status` (`ok`, `partial`, `error`), UTC `observed_at`, `data`, `warnings`, `truncated`, `redacted`, `omitted_count`, and `next_cursor` where relevant. These fields all fall within the result-size budget. Counts may be unknown and must not be fabricated.

Runtime failures have a stable machine-readable code such as `ACCESS_DENIED`, `SECRET_BLOCKED`, `DEPENDENCY_MISSING`, `UNAVAILABLE`, `UNSUPPORTED`, `INVALID_ARGUMENT`, `NOT_FOUND`, `TIMEOUT`, or `OUTPUT_LIMIT`. Never return raw exceptions or credential-bearing command output. Runtime failures use MCP error signaling consistently; validation/protocol errors follow the official SDK’s behavior. Partial observations contain the reason and fields that are unknown.

Process CPU observations require an explicit bounded sampling interval (proposed 100 ms) and return unknown when the process exits or cannot be read. Metrics with differing OS semantics document those differences. Permission failures must not be translated into zero usage, no listeners, or a stopped application.

## Validation and acceptance

Before declaring v1 complete:

1. Automated fixtures verify secret-source denial across read/list/search, Git diffs/history, path aliases/case variants, and renamed/deleted secret paths. Include multi-line keys, truncation boundaries, credentials in URLs, and secrets in errors/logs. Test fail-closed behavior on ambiguous paths.
2. Fake-executable and adapter tests prove fixed argument construction, shell/option injection resistance, deadlines, cancellation, bounded capture, Git helper suppression, and remote-Docker rejection without invoking real mutation commands.
3. HTTP tests cover loopback and IPv6, prohibited destinations, DNS rebinding protection, ignored proxy variables, denied redirects, paths/query strings, TLS failure, and bounded/redacted response content.
4. Native Windows, macOS, and Linux checks exercise files, processes, ports, metrics, and MCP stdio startup. OS permission restrictions are acceptable only when reported explicitly.
5. Temporary Git repositories and a disposable local container verify the allowed adapters. Missing Git/Docker and a stopped Docker daemon must leave unrelated tools working. Test setup may create fixtures; shipped tools cannot expose those mutation abilities.
6. An MCP client integration test verifies discovery, schema validation, structured results, error handling, concurrent calls, cancellation, and uncontaminated stdout.
7. Verify the launch recipe manually with at least one intended coding client. Document other clients as unverified until exercised; protocol compatibility alone is not a claim of tested integration.
8. Demonstrate “API not running” with a known fixture: distinguish missing listener, stopped container, failed health response, and permission-limited evidence. Compare observed working-tree/container state before and after inspection to catch unintended writes.

No product code, dependencies, or test fixtures are created as part of approving this specification.

## Deliverables and builder discretion

Implementation deliverables: Python source, locked local setup, tests, Windows/macOS/Linux launch examples, concise tool/reference documentation, secret-policy documentation, and an API-outage walkthrough. No distribution or deployment pipeline is required.

The builder chooses exact compatible package versions, module names, test framework, and equivalent implementation details. Security-sensitive behavior, cross-platform coverage, public tool schemas, and the local-only/read-only scope cannot be silently narrowed. Changes to those need to be reflected in the spec.

## References

- [Official MCP SDKs](https://modelcontextprotocol.io/docs/sdk): TypeScript and Python are both Tier 1.
- [Official MCP Python SDK](https://py.sdk.modelcontextprotocol.io/): Python SDK and runtime requirements; select a pinned stable release during implementation.
- [MCP stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports): client-launched subprocess, protocol-only stdout, stderr logging.
- [psutil documentation](https://psutil.io/): portable process, network, and system inspection, subject to OS permissions.
- [Python asyncio subprocess documentation](https://docs.python.org/3/library/asyncio-subprocess.html): executable/argument-based subprocess APIs and asynchronous collection.
