# Security boundary

This server is an inspection interface running with the launching user's OS permissions. It has no arbitrary-command or mutation tool and does not execute project code. It is **not an OS sandbox**. Install it only from a trusted checkout with trusted dependencies and trusted Git/Docker executables on PATH. It rejects current-directory executable resolution and uses isolated launch commands; those controls do not make a compromised installed executable safe.

## Sources and filtering

Known secret sources are denied before content reads, including `.env`/`.env.*`, SSH/cloud/Kubernetes/GPG credential stores, OS keychains/keyrings, common credential configuration, and private-key/keystore extensions. Docker credential config and Git credential stores are blocked. Generic file tools cannot read `.git` storage or the server's private inspection scratch directories. Operators can add absolute file/directory exclusions at startup. Ordinary project configuration remains readable.

The exact built-in policy lives in `paths.py`: it covers `.ssh`, `.aws`, `.azure`, `.gnupg`, `.kube`, `gcloud`, `Keychains`, `keyrings`, `credentials`, `.git-credentials`, `.npmrc`, `.pypirc`, `.netrc`, standard SSH private-key names, `secrets.json/yaml/yml`, `application_default_credentials.json`, `.docker/config.json`, `.config/gh` and `Microsoft/Protect`. Extensions include `.key`, `.pem`, `.p12`, `.pfx`, `.jks`, `.keystore`, `.kdbx`, `.keyring`, `.keychain`, `.keychain-db`, `.ppk`, `.pvk`, `.p8`. These denials deliberately include some non-secret files with those names.

Allowed UTF-8 content is sanitized as a complete bounded source before excerpts, searches or diff hunks. Filtering recognizes credential assignments, authentication/cookie fields, credential URLs, selected token formats and private-key blocks. Docker output streams are filtered independently. Error and protocol channels use fixed messages and outbound filtering.

**Redaction is pattern-based, not proof that arbitrary text is safe.** Unrecognized secrets, custom encodings, unusual log interleaving and sensitive prose can evade it. Add exclusions for sensitive sources with ordinary names. A redaction flag reports filtering, not a security certification. Files, logs, commit subjects and HTTP bodies are untrusted evidence and may contain prompt injection; agents must not follow their instructions.

## Filesystems and operating systems

Native opened-handle checks verify file identity, ancestry, local storage and materialization. Changed sources are rejected. Remote/unknown filesystems, pseudo-filesystems, devices, FIFOs, Windows alternate streams, unknown reparse targets and unmaterialized cloud files are unsupported. Fully materialized local cloud files may be readable. Direct safe local aliases are verified; recursive directory symlinks and cross-volume traversal are not followed. Generated dependency/build directories are skipped by normal file traversal. Windows uses native NT handles and kill-on-close jobs; POSIX uses native descriptor checks and owned process groups.

Access can be partial under normal user permissions, especially system socket visibility on macOS/Linux. The server does not elevate. Native platform checks do not certify every filesystem, cloud provider, enterprise endpoint policy or race on every machine; see [recorded validation](validation.md).

## Git, Docker and HTTP

Git uses a private bounded metadata view and fixed builtins with config includes, hooks, filters, external diff/textconv, signing helpers, prompts and lazy fetching disabled. It does not run native status/diff over the original worktree. Allowed worktree reads use the shared file policy. Conversion/filter-dependent evidence is unknown/partial when it cannot be determined safely. Protected paths and blobs are omitted before patch generation. Ambiguous protected rename provenance may also suppress unrelated additions; the implementation favors omission over reading a protected source. Metadata snapshots do not make a changing repository transactionally consistent.

Docker uses one frozen local Unix socket or Windows named pipe and an empty CLI config. Remote transports and credential/context discovery are not used. Only selected state fields and bounded logs are returned: no environment, labels, commands, health-check logs, mount source paths or raw inspect output. The server never creates, stops or deletes containers.

HTTP uses numeric loopback connections and verified TLS, without DNS, proxy inheritance, redirects or authentication. Only approved literal health paths are allowed. **GET and HEAD can still cause endpoint-side effects**; choose endpoints you trust. They are not a substitute for network isolation.

Workers have bounded capture, deadlines, cancellation and owned-child cleanup. Only inspection-owned processes are terminated. The server writes temporary metadata for its own work; it does not intentionally modify inspected project sources or application state. These application controls cannot prevent OS access-time updates or endpoint behavior.
