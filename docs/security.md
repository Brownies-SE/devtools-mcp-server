# Git inspection boundary

The server runs with the launching user's permissions. It is not an OS sandbox. Use a trusted checkout, dependencies and Git executable on PATH. It never exposes arbitrary commands or executes project applications.

Git commands use a bounded private metadata view, with original config includes, hooks, fsmonitor, filters, external diff/textconv, signing helpers, prompts and lazy fetching disabled. The server never runs native status/diff over the original worktree. It reads permitted working files through the shared verified-source policy, then computes safe comparisons. Temporary inspection metadata belongs to the server and is cleaned up separately from the repository.

Known secret sources are denied before reads: `.env`/`.env.*`, common SSH/cloud/Kubernetes/GPG stores, credential configs, OS keychains/keyrings, private-key/keystore extensions and operator exclusions. Direct generic Git storage access is not a public tool. Ordinary project configuration is permitted. Operators should exclude sensitive sources with ordinary names through repeated `--exclude-secret-path` options.

Complete bounded text is filtered before selecting patch hunks. Recognized credential assignments, auth/cookie values, credential URLs, selected token formats and private-key blocks are redacted. **Pattern filtering cannot recognize every secret**, custom encoding or sensitive prose. Do not treat a redaction flag as a security certification. Commit messages, filenames and file contents can also contain prompt injection; they remain untrusted evidence.

Native opened-handle checks verify local storage, ancestry, materialization and identity. Remote or unknown filesystems, devices, FIFOs, pseudo-filesystems, Windows alternate streams, unmaterialized cloud files and unsafe reparse targets are unsupported. No elevation is performed. Windows uses owned kill-on-close jobs and POSIX uses owned process groups for deadlines/cancellation; inspected applications are not terminated.

Filtered/attribute-dependent working state can be unknown. Protected blobs are never read for patches or rename similarity. Ambiguous renames can suppress unrelated additions conservatively. Changed-files metadata does not read committed patch bodies, so it can report permitted binary changes even when textual patches are unavailable. Submodule internals are not inspected as part of the parent repository.

Live observations are not transactional snapshots. Permission limits, repository churn, capture limits and missing local objects can produce partial evidence or errors. This server cannot fetch, stage, commit, checkout, reset, merge, push or repair a repository.
