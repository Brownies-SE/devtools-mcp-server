# Contributing

`main` contains stable releases. `dev` integrates reviewed changes.

1. Fetch the latest branches and create a topic branch from `origin/dev`.
2. Make and validate the change on that branch.
3. Push the branch and open a pull request targeting `dev`.
4. Address checks and review feedback before merging.

For example:

```sh
git fetch origin
git switch -c feat/my-change origin/dev
git push -u origin feat/my-change
gh pr create --base dev
```

Use `fix/...` for fixes and `chore/...` for repository maintenance. Documentation follows the same branch and PR process.

Pull requests to `main` must come from this repository's `dev` branch and represent a requested release. Do not push directly to either shared branch.

Describe the problem, resulting behavior, checks performed, and any checks not run. Never include credentials or real secrets in code, examples, logs, or test fixtures.
