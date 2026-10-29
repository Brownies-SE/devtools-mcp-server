# Git-only validation

The six-tool Git server replaces the broader DevTools implementation. Results for the former Docker/HTTP/process tools do not validate this interface.

Local target: macOS arm64, standard CPython 3.14.10, uv 0.13.6. Native CI targets standard CPython 3.14 on Windows x86-64, macOS arm64 and Linux x86-64. Dependencies are frozen in `uv.lock`.

```sh
uv sync --frozen --no-python-downloads
uv run --frozen pytest -q --tb=short
uv run --frozen ruff check src tests
uv run --frozen ruff format --check src tests
```

Focused Git checks cover SHA-1/SHA-256, root/unborn commits, linked worktrees, literal revisions, ignore rules, protected source/rename suppression, filter/include avoidance, safe patches and metadata-only changed-files. Real stdio checks discover exactly six tools, exercise every tool and validate strict errors, output schemas, channel consistency and result limits from a hostile unrelated cwd/PYTHONPATH. Native path and worker tests cover local identity, cancellation, owned-child cleanup and capacity/deadlines.

The Git-only branch's final local/native results and actual coding-client discovery are recorded in its pull request. No claim is made about every filesystem, cloud provider, Git extension or coding client. Pattern filtering is not proof that arbitrary text contains no secrets.
