import os
import stat
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import psutil

from .models import InspectionContext, InspectionError, StartupConfig

_SECRET_PARTS = {
    ".ssh",
    ".aws",
    ".azure",
    ".gnupg",
    ".kube",
    "gcloud",
    "keychains",
    "credentials",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "application_default_credentials.json",
}
_SECRET_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".jks", ".keystore", ".kdbx"}
_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target"}
_LOCAL_TYPES = {
    "apfs",
    "hfs",
    "ufs",
    "exfat",
    "msdos",
    "vfat",
    "ext2",
    "ext3",
    "ext4",
    "xfs",
    "btrfs",
    "f2fs",
    "tmpfs",
    "ramfs",
    "overlay",
    "zfs",
    "ntfs",
    "ntfs3",
    "refs",
    "fat",
    "fat32",
    "iso9660",
    "cd9660",
    "udf",
}


def _normalized(path) -> str:
    value = str(path).replace("\\", "/").rstrip("/")
    if sys.platform == "darwin":
        value = unicodedata.normalize("NFC", value)
    return value.casefold() if os.name == "nt" or sys.platform == "darwin" else value


def _within(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip("/") + "/")


@dataclass(frozen=True)
class VerifiedEntry:
    path: Path
    relative_path: str
    type: str
    size: int
    modified_at: float


class VerifiedTextFile:
    def __init__(self, proof, ctx: InspectionContext):
        self.proof = proof
        self.ctx = ctx

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.proof.close()

    def read_utf8(self) -> str:
        self.proof.verify()
        if self.proof.initial.st_size > 1048576:
            raise InspectionError("OUTPUT_LIMIT", "File exceeds the 1 MiB source limit.")
        buffer = bytearray()
        source = f"{self.proof.initial.st_dev}:{self.proof.initial.st_ino}:{self.proof.initial.st_mtime_ns}"
        while chunk := os.read(self.proof.fd, 16384):
            self.ctx.budget.consume("file_scan", len(chunk), source=source)
            buffer.extend(chunk)
        self.proof.verify()
        if b"\0" in buffer:
            raise InspectionError("UNSUPPORTED", "Binary source files are not returned.")
        try:
            return bytes(buffer).decode("utf-8-sig")
        except UnicodeDecodeError:
            raise InspectionError("UNSUPPORTED", "Only valid UTF-8 text is supported.") from None


@dataclass
class VerifiedLocation:
    proof: object

    @property
    def path(self):
        return self.proof.path

    @property
    def fd(self):
        return self.proof.fd

    @property
    def device(self):
        return self.proof.initial.st_dev

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.proof.close()


class PathPolicy:
    def __init__(self, config: StartupConfig):
        self.config = config
        self.exclusions = {_normalized(value) for value in config.extra_secret_paths}
        for value in config.extra_secret_paths:
            try:
                self.exclusions.add(_normalized(Path(value).resolve(strict=False)))
            except OSError, RuntimeError, ValueError:
                pass
        try:
            self.mounts = sorted(
                (
                    (_normalized(item.mountpoint), item.fstype.casefold())
                    for item in psutil.disk_partitions(all=True)
                ),
                key=lambda item: len(item[0]),
                reverse=True,
            )
        except OSError, psutil.Error:
            raise InspectionError(
                "UNSUPPORTED", "Local filesystem mounts cannot be verified."
            ) from None

    def deny(self, path: Path, *, metadata=False) -> None:
        names = [part.casefold() for part in path.parts]
        if any(
            part in _SECRET_PARTS
            or part == ".env"
            or part.startswith(".env.")
            or Path(part).suffix in _SECRET_SUFFIXES
            for part in names
        ):
            raise InspectionError("SECRET_BLOCKED", "Known secret sources are off limits.")
        if not metadata and ".git" in names:
            raise InspectionError("SECRET_BLOCKED", "Direct Git storage access is not exposed.")
        if any(
            names[index : index + 2]
            in [[".docker", "config.json"], [".config", "gh"], [".local", "keyrings"]]
            for index in range(len(names) - 1)
        ):
            raise InspectionError("SECRET_BLOCKED", "Credential configuration is off limits.")
        if os.name != "nt" and path.parts[:2] in {("/", "proc"), ("/", "sys"), ("/", "dev")}:
            raise InspectionError("UNSUPPORTED", "Pseudo-filesystem sources are not exposed.")
        normalized = _normalized(path)
        if any(_within(normalized, _normalized(item)) for item in self.exclusions):
            raise InspectionError("SECRET_BLOCKED", "Source is excluded by the operator.")

    def _mount_hint(self, path: Path) -> None:
        normalized = _normalized(path)
        for mount, kind in self.mounts:
            if _within(normalized, mount):
                if kind not in _LOCAL_TYPES:
                    raise InspectionError(
                        "UNSUPPORTED", "Remote, virtual or unverified filesystem."
                    )
                return
        raise InspectionError("UNSUPPORTED", "Filesystem locality cannot be established.")

    def _canonical(self, value: str, *, metadata=False):
        if not isinstance(value, str) or not value or len(value) > 4096 or "\0" in value:
            raise InspectionError("INVALID_ARGUMENT", "An absolute local path is required.")
        path = Path(value)
        if not path.is_absolute() or value.startswith(("//", "\\\\")):
            raise InspectionError("INVALID_ARGUMENT", "An absolute non-network path is required.")
        self.deny(path, metadata=metadata)
        self._mount_hint(path)
        if os.name == "nt":
            resolved = path.resolve(strict=True)
            self.deny(resolved, metadata=metadata)
            self._mount_hint(resolved)
            return resolved, []
        aliases = []
        remaining = list(path.parts[1:])
        current = Path("/")
        links = 0
        while remaining:
            component = remaining.pop(0)
            if component == "..":
                current = current.parent
                continue
            candidate = current / component
            self.deny(candidate, metadata=metadata)
            self._mount_hint(candidate)
            info = os.lstat(candidate)
            if stat.S_ISLNK(info.st_mode):
                links += 1
                if links > 40:
                    raise InspectionError("ACCESS_DENIED", "Alias resolution is ambiguous.")
                target = os.readlink(candidate)
                aliases.append((candidate, info, target))
                full = Path(target) if os.path.isabs(target) else current / target
                self.deny(full, metadata=metadata)
                full = Path(os.path.normpath(str(full / Path(*remaining))))
                self.deny(full, metadata=metadata)
                self._mount_hint(full)
                remaining = list(full.parts[1:])
                current = Path("/")
            else:
                current = candidate
        return current, aliases

    def _open(self, path: Path, aliases, ctx, *, directory=False, metadata=False):
        def allow(actual):
            self.deny(actual, metadata=metadata)

        if os.name == "nt":
            from .platforms import windows

            opener = getattr(windows, "open_verified", None)
            if opener is None:
                raise InspectionError(
                    "UNSUPPORTED", "Native Windows path verification is unavailable."
                )
        else:
            from .platforms.posix import open_verified

            opener = open_verified
        return opener(path, directory, allow, aliases, ctx)

    def open_text(self, path: str, ctx: InspectionContext) -> VerifiedTextFile:
        try:
            resolved, aliases = self._canonical(path)
            proof = self._open(resolved, aliases, ctx)
            return VerifiedTextFile(proof, ctx)
        except OSError as error:
            from .platforms.posix import map_os_error

            raise map_os_error(error) from None

    def check_storage(self, path: str, purpose: str, ctx: InspectionContext) -> VerifiedLocation:
        try:
            resolved, aliases = self._canonical(path, metadata=purpose == "git")
            directory = stat.S_ISDIR(os.lstat(resolved).st_mode)
            return VerifiedLocation(
                self._open(resolved, aliases, ctx, directory=directory, metadata=purpose == "git")
            )
        except OSError as error:
            from .platforms.posix import map_os_error

            raise map_os_error(error) from None

    def iter_entries(self, directory: str, recursive: bool, ctx: InspectionContext):
        resolved, aliases = self._canonical(directory)
        root = self._open(resolved, aliases, ctx, directory=True)
        try:
            yield from self._walk(root, resolved, root.initial.st_dev, recursive, ctx)
        finally:
            root.close()

    def _walk(self, proof, root: Path, device: int, recursive: bool, ctx):
        proof.verify()
        with os.scandir(str(proof.path) if os.name == "nt" else proof.fd) as entries:
            for entry in entries:
                ctx.budget.visit_entry()
                if time.monotonic() >= ctx.deadline:
                    raise InspectionError("TIMEOUT", "File traversal timed out.")
                child = proof.path / entry.name
                try:
                    self.deny(child)
                    self._mount_hint(child)
                except InspectionError:
                    continue
                info = entry.stat(follow_symlinks=False)
                if getattr(info, "st_flags", 0) & 0x40000000:
                    continue
                alias = stat.S_ISLNK(info.st_mode) or bool(
                    getattr(info, "st_file_attributes", 0) & 0x400
                )
                is_dir = stat.S_ISDIR(info.st_mode) and not alias
                if alias:
                    try:
                        self._canonical(str(child))
                    except InspectionError, OSError:
                        continue
                if not (stat.S_ISREG(info.st_mode) or is_dir or alias):
                    continue
                if entry.name in _SKIP and recursive:
                    continue
                proof.verify()
                yield VerifiedEntry(
                    child,
                    child.relative_to(root).as_posix(),
                    "symlink" if alias else ("directory" if is_dir else "file"),
                    info.st_size,
                    info.st_mtime,
                )
                if recursive and is_dir and info.st_dev == device:
                    opened = self._open(child, [], ctx, directory=True)
                    try:
                        yield from self._walk(opened, root, device, recursive, ctx)
                    finally:
                        opened.close()
        proof.verify()
