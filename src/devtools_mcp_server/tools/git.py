import codecs
import hashlib
import json
import os
import re
import shutil
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from ..execution import run_command
from ..models import InspectionError, PageArgs, Reply, StrictModel, Warning
from ..pagination import PagePosition, decode_cursor, encode_cursor, page
from ..paths import PathPolicy, VerifiedTextFile, read_git_metadata


class GitStatusArgs(StrictModel):
    repository: str = Field(min_length=1, max_length=4096)


class GitBranchesArgs(PageArgs):
    repository: str = Field(min_length=1, max_length=4096)


class GitLogArgs(GitBranchesArgs):
    revision: str | None = Field(default=None, min_length=1, max_length=256)


class GitStatusData(StrictModel):
    branch: str | None
    staged: list[str]
    unstaged: list[str]
    untracked: list[str]
    unknown: list[str]


class GitBranch(StrictModel):
    name: str
    commit: str
    current: bool
    tracking_remote: str | None = None
    tracking_ref: str | None = None


class GitBranchesData(StrictModel):
    branches: list[GitBranch]


class GitCommit(StrictModel):
    commit: str
    subject: str
    author: str
    authored_at: str


class GitLogData(StrictModel):
    commits: list[GitCommit]


def _error(code="UNSUPPORTED", message="Repository metadata cannot be inspected safely."):
    return InspectionError(code, message)


def _ref(value):
    if (
        not value
        or len(value) > 256
        or value.startswith(("-", "/", "."))
        or value.endswith(("/", ".", ".lock"))
        or any(piece in value for piece in ["..", "@{", "//"])
    ):
        raise _error("INVALID_ARGUMENT", "Only literal local branch and tag names are permitted.")
    if re.search(r"[\x00-\x20\x7f~^:?*\[\\]", value) or any(
        part.startswith(".") or part.endswith(".lock") for part in value.split("/")
    ):
        raise _error("INVALID_ARGUMENT", "Only literal local branch and tag names are permitted.")
    return value


def _revision(value):
    if value == "HEAD" or re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", value):
        return value
    return _ref(value)


def _text(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _error(message="Git metadata is not valid UTF-8.") from None


@dataclass(frozen=True)
class ObjectEntry:
    mode: str
    oid: str


class GitReadView:
    def __init__(self, repository, ctx):
        self.ctx = ctx
        self.policy = PathPolicy(ctx.config)
        self.stack = ExitStack()
        self.locations = []
        self.snapshots = {}
        self.metadata_dir = ctx.scratch_dir / "git"
        self.ignore_tree = ctx.scratch_dir / "ignore-tree"
        self.config = {}
        self.warnings = []
        self.worktree = Path(repository)
        self.object_format = "sha1"
        self.conversion_unknown = False
        self.ignore_unknown = False

    def warning(
        self,
        code="UNSUPPORTED",
        message="Some working classifications require unsupported configuration.",
    ):
        if len(self.warnings) < 10 and not any(
            item.code == code and item.message == message for item in self.warnings
        ):
            self.warnings.append(Warning(code=code, message=message))

    def __enter__(self):
        try:
            location = self.location(self.worktree)
            self.worktree = location.path
            marker = self.location(self.worktree / ".git")
            if marker.proof.directory:
                self.storage = marker.path
            else:
                pointer = _text(self.read(self.worktree, ".git")).strip()
                if not pointer.startswith("gitdir: ") or "\n" in pointer:
                    raise _error()
                target = Path(pointer[8:])
                self.storage = target if target.is_absolute() else self.worktree / target
                self.location(self.storage)
            common = self.maybe(self.storage, "commondir")
            self.common = self.storage
            if common is not None:
                value = _text(common).strip()
                if not value or "\n" in value:
                    raise _error()
                target = Path(value)
                self.common = target if target.is_absolute() else self.storage / target
                self.common = self.location(self.common).path
            self.metadata_dir.mkdir()
            self.ignore_tree.mkdir()
            self.stack.callback(shutil.rmtree, self.metadata_dir)
            self.stack.callback(shutil.rmtree, self.ignore_tree)
            (self.metadata_dir / "objects" / "info").mkdir(parents=True)
            (self.metadata_dir / "refs").mkdir()
            self.copy(self.storage, "HEAD")
            self.copy(self.storage, "index", optional=True)
            for name in ["packed-refs", "shallow", "info/exclude"]:
                self.copy(self.common, name, optional=True)
            refs = self.common / "refs"
            if refs.is_dir():
                for entry in self.policy.iter_entries(
                    str(refs), True, self.ctx, metadata=True, skip_generated=False
                ):
                    if entry.type == "file":
                        self.copy(self.common, "refs/" + entry.relative_path)
                    elif entry.type != "directory":
                        raise _error(message="Aliased Git references are unsupported.")
            version = self.command("--version").stdout
            match = re.search(rb"git version (\d+)\.(\d+)", version)
            if not match or tuple(map(int, match.groups())) < (2, 45):
                raise _error(
                    message="Git 2.45 or newer is required for local-only object inspection."
                )
            # Global includes are never followed. Their presence makes affected
            # classifications unknown rather than inventing clean/untracked state.
            home = os.environ.get("USERPROFILE" if os.name == "nt" else "HOME")
            if home:
                for folder, key in [
                    (Path(home), ".gitconfig"),
                    (Path(home) / ".config" / "git", "config"),
                ]:
                    raw = self.maybe(folder, key)
                    if raw is not None:
                        self.config.update(self.parse_config(raw))
            raw_config = self.read(self.common, "config")
            self.config.update(self.parse_config(raw_config))
            worktree_config = self.maybe(self.storage, "config.worktree")
            if worktree_config is not None:
                self.config.update(self.parse_config(worktree_config))
            self.object_format = self.config.get("extensions.objectformat", "sha1")
            if (
                self.object_format not in {"sha1", "sha256"}
                or self.config.get("extensions.refstorage", "files") != "files"
                or self.config.get("core.repositoryformatversion", "0") not in {"0", "1"}
            ):
                raise _error(message="Repository object or ref format is unsupported.")
            self.conversion_unknown = any(
                key.startswith(("filter.", "include.", "includeif."))
                or key in {"core.attributesfile", "core.eol", "core.safecrlf"}
                for key in self.config
            ) or self.config.get("core.autocrlf", "false").lower() not in {
                "false",
                "no",
                "off",
                "0",
            }
            self.ignore_unknown = any(
                key.startswith(("include.", "includeif.")) for key in self.config
            )
            config = (
                "[core]\nrepositoryformatversion = "
                + "1"
                + "\nbare = false\nfilemode = false\n[extensions]\nobjectformat = "
                + self.object_format
                + "\n"
            )
            (self.metadata_dir / "config").write_text(config, encoding="utf-8")
            stores = self.verify_store(self.common / "objects", set())
            (self.metadata_dir / "objects" / "info" / "alternates").write_text(
                "\n".join(path.as_posix() for path in stores) + "\n", encoding="utf-8"
            )
            self.verify()
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        self.stack.close()

    def location(self, path):
        location = self.stack.enter_context(self.policy.check_storage(str(path), "git", self.ctx))
        self.locations.append(location)
        return location

    @staticmethod
    def stamp(path):
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) in {
            0xA0000003,
            0xA000000C,
        }:
            raise _error(message="Aliased Git metadata is unsupported.")
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    def read(self, storage, key):
        path = Path(storage) / key
        try:
            before = self.stamp(path)
        except FileNotFoundError:
            raise _error("NOT_FOUND", "Git metadata is absent.") from None
        raw = read_git_metadata(str(storage), key, self.ctx)
        if before != self.stamp(path):
            raise _error("ACCESS_DENIED", "Git metadata changed during inspection.")
        self.snapshots[path] = before
        return raw

    def maybe(self, storage, key):
        try:
            return self.read(storage, key)
        except InspectionError as error:
            if error.code == "NOT_FOUND":
                return None
            raise

    def copy(self, storage, key, optional=False):
        raw = self.maybe(storage, key) if optional else self.read(storage, key)
        if raw is not None:
            output = self.metadata_dir / key
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(raw)

    def parse_config(self, raw):
        (self.ctx.scratch_dir / "raw-config").write_bytes(raw)
        capture = self.command(
            "config",
            "--file=" + str(self.ctx.scratch_dir / "raw-config"),
            "--no-includes",
            "--null",
            "--list",
        )
        if capture.returncode:
            raise _error(message="Git configuration syntax is unsupported.")
        result = {}
        for item in capture.stdout.split(b"\0"):
            if not item:
                continue
            key, _, value = item.partition(b"\n")
            result[_text(key)] = _text(value)
        return result

    def verify_store(self, directory, visited):
        if len(visited) >= 8:
            raise _error(message="Object alternate graph is too large.")
        location = self.location(directory)
        directory = location.path
        if directory in visited:
            return []
        if any(char in str(directory) for char in '\n\r"'):
            raise _error(message="Ambiguous object storage path.")
        visited.add(directory)
        # Verify all object paths before Git can resolve them. No blob contents
        # are copied or read by this metadata validation.
        for entry in self.policy.iter_entries(
            str(directory), True, self.ctx, metadata=True, skip_generated=False
        ):
            if entry.type == "symlink":
                raise _error(message="Aliased object storage is unsupported.")
            with self.policy.check_storage(str(entry.path), "git", self.ctx):
                pass
        if self.maybe(directory, "info/http-alternates"):
            raise _error(message="Remote object alternates are unsupported.")
        alternates = self.maybe(directory, "info/alternates")
        stores = [directory]
        if alternates:
            for line in _text(alternates).splitlines():
                if not line or line.startswith('"'):
                    raise _error(message="Ambiguous object alternates are unsupported.")
                target = Path(line)
                stores.extend(
                    self.verify_store(
                        target if target.is_absolute() else directory / target, visited
                    )
                )
        return stores

    def verify(self):
        for location in self.locations:
            location.proof.verify()
        for path, stamp in self.snapshots.items():
            try:
                unchanged = self.stamp(path) == stamp
            except FileNotFoundError:
                unchanged = False
            if not unchanged:
                raise _error("ACCESS_DENIED", "Git metadata changed during inspection.")

    def command(self, *args, capture="ordinary", ignore_tree=False):
        if args[0] in {"--version", "version"}:
            return run_command("git", tuple(args), self.ctx)
        self.verify()
        worktree = self.ignore_tree if ignore_tree else self.worktree
        result = run_command(
            "git",
            ("--git-dir=" + str(self.metadata_dir), "--work-tree=" + str(worktree), *args),
            self.ctx,
            capture=capture,
        )
        self.verify()
        return result

    def commit(self, revision, *, unborn=False):
        value = _revision(revision)
        candidates = (
            [value]
            if value == "HEAD" or re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", value)
            else (
                [value]
                if value.startswith(("refs/heads/", "refs/tags/"))
                else ["refs/heads/" + value, "refs/tags/" + value]
            )
        )
        for candidate in candidates:
            result = self.command(
                "rev-parse", "--verify", "--end-of-options", candidate + "^{commit}"
            )
            if result.returncode == 0:
                commit = _text(result.stdout).strip()
                if re.fullmatch(
                    r"[a-f0-9]{40}" if self.object_format == "sha1" else r"[a-f0-9]{64}", commit
                ):
                    return commit
                raise _error()
        head = _text((self.metadata_dir / "HEAD").read_bytes()).strip()
        if unborn and value == "HEAD" and head.startswith("ref: "):
            ref = head[5:]
            _ref(ref)
            packed = (
                (self.metadata_dir / "packed-refs").read_text()
                if (self.metadata_dir / "packed-refs").exists()
                else ""
            )
            if not (self.metadata_dir / ref).exists() and not any(
                line.endswith(" " + ref) for line in packed.splitlines()
            ):
                return None
        raise _error("NOT_FOUND", "Requested commit is not available locally.")

    def current_branch(self):
        value = _text((self.metadata_dir / "HEAD").read_bytes()).strip()
        return value[16:] if value.startswith("ref: refs/heads/") else None

    def index(self):
        capture = self.command("ls-files", "--stage", "-z")
        if capture.returncode:
            raise _error()
        rows = {}
        for record in capture.stdout.split(b"\0"):
            if not record:
                continue
            header, path = record.split(b"\t", 1)
            mode, oid, stage = header.split(b" ")
            name = _text(path)
            rows[name] = (
                ObjectEntry(_text(mode), _text(oid))
                if stage == b"0"
                else ObjectEntry("conflict", "")
            )
        return rows

    def tree(self, commit):
        if commit is None:
            return {}
        capture = self.command("ls-tree", "-r", "-z", "--full-tree", commit)
        if capture.returncode:
            raise _error("UNAVAILABLE", "Required tree objects are not available locally.")
        rows = {}
        for record in capture.stdout.split(b"\0"):
            if not record:
                continue
            header, path = record.split(b"\t", 1)
            mode, _, oid = header.split(b" ")
            rows[_text(path)] = ObjectEntry(_text(mode), _text(oid))
        return rows

    def allowed(self, relative):
        try:
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or not relative:
                return False
            self.policy.deny(self.worktree / path)
            return True
        except InspectionError:
            return False

    def working(self, relative):
        resolved, aliases = self.policy._canonical(str(self.worktree / relative))
        proof = self.policy._open(resolved, aliases, self.ctx)
        with VerifiedTextFile(proof, self.ctx) as source:
            raw = source.read_bytes()
            executable = bool(source.proof.initial.st_mode & 0o111)
        oid = hashlib.new(
            self.object_format, b"blob " + str(len(raw)).encode() + b"\0" + raw
        ).hexdigest()
        return ObjectEntry(
            "100755"
            if executable and self.config.get("core.filemode", "false") == "true"
            else "100644",
            oid,
        ), raw

    def untracked(self, index):
        candidates = []
        for entry in self.policy.iter_entries(
            str(self.worktree), True, self.ctx, skip_generated=False
        ):
            if entry.type != "file":
                continue
            name = entry.relative_path
            if Path(name).name == ".gitattributes":
                self.conversion_unknown = True
            if Path(name).name == ".gitignore":
                with self.policy.open_text(str(entry.path), self.ctx) as source:
                    content = source.read_utf8()
                target = self.ignore_tree / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            if name not in index:
                candidates.append(name)
        excludes = self.config.get("core.excludesfile")
        if excludes:
            try:
                source_path = Path(excludes).expanduser()
                if not source_path.is_absolute():
                    source_path = self.worktree / source_path
                with self.policy.open_text(str(source_path), self.ctx) as source:
                    content = source.read_utf8()
                target = self.ctx.scratch_dir / "safe-excludes"
                target.write_text(content, encoding="utf-8")
                with (self.metadata_dir / "config").open("a", encoding="utf-8") as config:
                    config.write(
                        "[core]\nexcludesFile = "
                        + json.dumps(target.as_posix(), ensure_ascii=False)
                        + "\n"
                    )
            except InspectionError as error:
                self.ignore_unknown = True
                self.warning(error.code, "Configured ignore source cannot be safely inspected.")
        if self.ignore_unknown:
            self.warning(
                message="Untracked visibility depends on unsupported ignore configuration."
            )
            return []
        ignored = set()
        for start in range(0, len(candidates), 100):
            capture = self.command(
                "check-ignore",
                "--no-index",
                "--",
                *(str(self.ignore_tree / name) for name in candidates[start : start + 100]),
                ignore_tree=True,
            )
            if capture.returncode not in {0, 1}:
                self.warning(message="Untracked ignore classification is unavailable.")
                return []
            for item in capture.stdout.splitlines():
                if item.startswith(b'"') and item.endswith(b'"'):
                    item = codecs.escape_decode(item[1:-1])[0]
                ignored.add(Path(_text(item)).relative_to(self.ignore_tree).as_posix())
        return sorted(set(candidates) - ignored)


def open_git_view(repository, ctx):
    return GitReadView(repository, ctx)


def resolve_commit(repository, revision, ctx):
    _revision(revision)
    with open_git_view(repository, ctx) as view:
        return view.commit(revision)


def git_status(args: GitStatusArgs, ctx) -> Reply[GitStatusData]:
    with open_git_view(args.repository, ctx) as view:
        index = view.index()
        head = view.tree(view.commit("HEAD", unborn=True))
        staged = sorted(
            path
            for path in index.keys() | head.keys()
            if index.get(path) != head.get(path) and view.allowed(path)
        )
        untracked = view.untracked(index)
        unstaged, unknown = [], []
        for path, item in index.items():
            if not view.allowed(path):
                continue
            if item.mode not in {"100644", "100755"} or view.conversion_unknown:
                unknown.append(path)
                view.warning()
                continue
            try:
                working, _ = view.working(path)
                if working != item:
                    unstaged.append(path)
            except FileNotFoundError:
                unstaged.append(path)
            except InspectionError as error:
                if error.code == "NOT_FOUND":
                    unstaged.append(path)
                else:
                    unknown.append(path)
                    view.warning(error.code, "Some working sources cannot be safely inspected.")
        return Reply[GitStatusData](
            data=GitStatusData(
                branch=view.current_branch(),
                staged=staged,
                unstaged=sorted(unstaged),
                untracked=untracked,
                unknown=sorted(unknown),
            ),
            status="partial" if view.warnings else "ok",
            warnings=view.warnings,
        )


def git_branches(args: GitBranchesArgs, ctx) -> Reply[GitBranchesData]:
    with open_git_view(args.repository, ctx) as view:
        capture = view.command(
            "for-each-ref", "--format=%(refname)%00%(objectname)%00", "refs/heads/"
        )
        if capture.returncode:
            raise _error()
        branches = []
        for line in capture.stdout.splitlines():
            ref, oid, _ = line.split(b"\0")
            name = _text(ref)[11:]
            branches.append(
                GitBranch(
                    name=name,
                    commit=_text(oid),
                    current=name == view.current_branch(),
                    tracking_remote=view.config.get("branch." + name + ".remote"),
                    tracking_ref=view.config.get("branch." + name + ".merge"),
                )
            )
        branches.sort(key=lambda branch: branch.name)
        rows, cursor = page(branches, args, "git_branches", {"repository": args.repository})
        return Reply[GitBranchesData](data=GitBranchesData(branches=rows), next_cursor=cursor)


def git_log(args: GitLogArgs, ctx) -> Reply[GitLogData]:
    filters = {"repository": args.repository, "revision": args.revision}
    position = decode_cursor(args.cursor, "git_log", filters) if args.cursor else PagePosition()
    with open_git_view(args.repository, ctx) as view:
        commit = view.commit(
            position.anchor_commit or args.revision or "HEAD", unborn=args.revision is None
        )
        if commit is None:
            return Reply[GitLogData](data=GitLogData(commits=[]))
        capture = view.command(
            "log",
            "--no-show-signature",
            "--format=%H%x00%s%x00%an%x00%at%x00",
            "--max-count=" + str(args.limit + 1),
            "--skip=" + str(position.offset),
            commit,
            "--",
        )
        if capture.returncode:
            raise _error("UNAVAILABLE", "Required history objects are not available locally.")
        commits = []
        for line in capture.stdout.splitlines():
            oid, subject, author, timestamp, _ = line.split(b"\0")
            commits.append(
                GitCommit(
                    commit=_text(oid),
                    subject=_text(subject),
                    author=_text(author),
                    authored_at=datetime.fromtimestamp(int(timestamp), UTC).isoformat(),
                )
            )
        more = len(commits) > args.limit
        cursor = (
            encode_cursor("git_log", filters, PagePosition(position.offset + args.limit, commit))
            if more
            else None
        )
        return Reply[GitLogData](data=GitLogData(commits=commits[: args.limit]), next_cursor=cursor)
