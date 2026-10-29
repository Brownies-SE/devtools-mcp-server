# Troubleshooting with evidence

For “my API on port 8000 is down,” an agent can compose these calls:

1. `environment_info {}` identifies available tools and limits.
2. `ports_list {"port":8000,"protocol":"tcp"}` looks for a listener. An empty **partial** result cannot prove there is none.
3. `http_check {"url":"http://localhost:8000/health"}` distinguishes a response from a failed connection. HTTP 503 proves the endpoint answered unsuccessfully; `UNAVAILABLE` means no usable HTTP observation, which may be connection, TLS or parsing failure.
4. `processes_list` narrows relevant processes without reading command-line arguments or environment variables.
5. `docker_containers {"include_stopped":true}` and `docker_inspect` can show a relevant container stopped or unhealthy. Container state alone does not prove which application dependency failed.
6. `docker_logs` can provide bounded recent sanitized messages. `git_status`/`git_diff` and `files_search`/`files_read` can locate relevant permitted changes.
7. Explain observed facts, warnings and remaining uncertainty separately. Suggest a repair for the user to perform; this server cannot restart or change anything.

This is an example investigation, not a claim that an agent autonomously diagnosed a real outage. Deterministic fixtures verify 503, no listener, permission-limited evidence, and stopped container state where live Docker is provisioned.

## Common launch and inspection problems

| Symptom | Action |
| --- | --- |
| Module not found | Run `uv sync --frozen --no-python-downloads` in the checkout; use its absolute `.venv` Python, not system Python. |
| Launch hangs in a terminal | stdio waits for an MCP client. Connect through the client's MCP configuration. |
| Paths with spaces fail | Use separate JSON command and argument array; do not concatenate a shell command. |
| `DEPENDENCY_MISSING` | Install the optional CLI through your normal local tooling, then restart the MCP session. |
| Docker unavailable | Start your local engine yourself and select an unambiguous local endpoint. A socket file alone does not prove daemon readiness. |
| `SECRET_BLOCKED` | Use a permitted source. Operator options can add exclusions, not disable them. |
| `UNSUPPORTED` | Check filesystem/materialization, Git version/layout or HTTP body policy; do not bypass safety checks. |
| Partial sockets/processes | OS permissions or process churn limited observation; inspect warnings and avoid false “nothing running” conclusions. |
| Partial Git evidence | Filters/attributes, protected sources, missing objects or limits may prevent classification; do not treat omitted paths as clean. |
| `TIMEOUT` / `OUTPUT_LIMIT` | Narrow directory, PID, log tail, path filters or excerpt. |
| `BUSY` | Wait for active calls to finish or cancel them; there is no queue. |
| TLS failure | Use a valid trusted certificate for the original hostname. There is no insecure verification option. |
