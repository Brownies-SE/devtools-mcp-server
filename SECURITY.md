# Security

This project is under active development. Report suspected vulnerabilities privately through [GitHub security advisories](https://github.com/Brownies-SE/devtools-mcp-server/security/advisories/new), rather than including sensitive details in public issues.

Never submit real credentials, private keys, environment files, personal logs, or raw machine configuration in reports or pull requests. Use minimal fake examples.

## Repository controls

Only the owner currently has write access. Public visibility permits viewing and forking; it does not grant push access.

All branches block force pushes and deletion. `main` and `dev` require a pull request, resolved review conversations, an up-to-date branch, and successful branch-policy and native Python checks. Rules apply to administrators too, with no bypass actors. As a solo-maintainer repository, external approvals are not required; CODEOWNERS identifies the responsible maintainer without blocking the owner from merging their own PRs.

Changes target `dev`; release PRs target `main` from this repository's `dev` branch. Topic branches permit normal pushes by writers. Tags cannot be rewritten or deleted.

Actions use read-only tokens, cannot approve pull requests, and are restricted to the explicitly allowed, commit-pinned CI actions. Outside contributors' workflows require approval before execution. Secret scanning, push protection, and Dependabot vulnerability alerts are enabled in repository settings. Supported secret patterns are not a guarantee that every possible secret will be detected.

Repository owners can edit repository settings; GitHub rules cannot prevent an owner from changing their own rules. Account login security remains controlled by the account owner.
