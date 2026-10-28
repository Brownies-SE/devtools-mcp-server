import ctypes
import errno
import os
import stat
import struct
import sys
from contextlib import ExitStack
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..models import InspectionError

_DATALESS = 0x40000000
_LOCAL_LINUX = {
    0xEF53,
    0x58465342,
    0x9123683E,
    0xF2F52010,
    0x01021994,
    0x858458F6,
    0x794C7630,
    0x2FC12FC1,
    0x2011BAB0,
    0x4D44,
    0x9660,
    0x15013346,
}


@lru_cache(maxsize=1)
def _statfs_function():
    library = ctypes.CDLL(None, use_errno=True)
    function = library.fstatfs64 if sys.platform == "darwin" else library.fstatfs
    function.argtypes = [ctypes.c_int, ctypes.c_void_p]
    function.restype = ctypes.c_int
    return function


def filesystem_is_local(fd: int) -> bool:
    buffer = ctypes.create_string_buffer(4096)
    if _statfs_function()(fd, buffer) != 0:
        raise InspectionError("UNSUPPORTED", "Filesystem locality cannot be established.")
    if sys.platform == "darwin":
        # Darwin's statfs64 ABI: exported flags at 64, filesystem name at 72.
        flags = struct.unpack_from("=I", buffer.raw, 64)[0]
        name = buffer.raw[72:88].split(b"\0", 1)[0]
        return bool(flags & 0x1000) and name in {
            b"apfs",
            b"hfs",
            b"ufs",
            b"exfat",
            b"msdos",
            b"cd9660",
            b"udf",
        }
    if sys.platform.startswith("linux"):
        magic = ctypes.c_long.from_buffer(buffer).value & 0xFFFFFFFF
        return magic in _LOCAL_LINUX
    return False


def descriptor_path(fd: int) -> Path:
    if sys.platform == "darwin":
        import fcntl

        raw = fcntl.fcntl(fd, 50, bytes(1024))
        return Path(os.fsdecode(raw.split(b"\0", 1)[0]))
    if sys.platform.startswith("linux"):
        return Path(os.readlink(f"/proc/self/fd/{fd}"))
    raise InspectionError("UNSUPPORTED", "Verified descriptor paths are unavailable.")


def identity(info):
    return info.st_dev, info.st_ino


def check_materialized(info) -> None:
    if getattr(info, "st_flags", 0) & _DATALESS:
        raise InspectionError(
            "UNSUPPORTED", "Cloud placeholders are not hydrated during inspection."
        )


@dataclass
class VerifiedHandle:
    path: Path
    fd: int
    initial: os.stat_result
    directory: bool
    stack: ExitStack
    trace: list
    aliases: list
    allow: object

    def verify(self) -> None:
        for parent, name, child in self.trace:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if identity(current) != identity(os.fstat(child)):
                raise InspectionError("ACCESS_DENIED", "Path changed during handle verification.")
        for path, original, target in self.aliases:
            current = os.lstat(path)
            if identity(current) != identity(original) or os.readlink(path) != target:
                raise InspectionError("ACCESS_DENIED", "Alias changed during inspection.")
        actual = os.fstat(self.fd)
        check_materialized(actual)
        if not filesystem_is_local(self.fd):
            raise InspectionError("UNSUPPORTED", "Remote, virtual or unverified filesystem.")
        self.allow(descriptor_path(self.fd))
        if not self.directory and (actual.st_size, actual.st_mtime_ns, actual.st_ctime_ns) != (
            self.initial.st_size,
            self.initial.st_mtime_ns,
            self.initial.st_ctime_ns,
        ):
            raise InspectionError("ACCESS_DENIED", "Source changed during inspection.")

    def close(self) -> None:
        self.stack.close()


def open_verified(path: Path, directory: bool, allow, aliases, ctx) -> VerifiedHandle:
    stack = ExitStack()
    trace = []
    try:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
        parent = os.open("/", flags | os.O_DIRECTORY)
        stack.callback(os.close, parent)
        if not filesystem_is_local(parent):
            raise InspectionError("UNSUPPORTED", "Unverified root filesystem.")
        components = path.parts[1:]
        fd = parent
        for index, name in enumerate(components):
            is_directory = index < len(components) - 1 or directory
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            check_materialized(before)
            if not (stat.S_ISDIR(before.st_mode) if is_directory else stat.S_ISREG(before.st_mode)):
                raise InspectionError(
                    "UNSUPPORTED", "Only ordinary local files and directories are supported."
                )
            fd = os.open(name, flags | (os.O_DIRECTORY if is_directory else 0), dir_fd=parent)
            stack.callback(os.close, fd)
            opened = os.fstat(fd)
            if not (stat.S_ISDIR(opened.st_mode) if is_directory else stat.S_ISREG(opened.st_mode)):
                raise InspectionError(
                    "UNSUPPORTED", "Opened handle is not an ordinary file or directory."
                )
            if identity(before) != identity(opened):
                raise InspectionError("ACCESS_DENIED", "Source was replaced while opening.")
            check_materialized(opened)
            if not filesystem_is_local(fd):
                raise InspectionError("UNSUPPORTED", "Remote, virtual or unverified filesystem.")
            trace.append((parent, name, fd))
            parent = fd
        handle = VerifiedHandle(path, fd, os.fstat(fd), directory, stack, trace, aliases, allow)
        handle.verify()
        return handle
    except BaseException:
        stack.close()
        raise


def map_os_error(error: OSError) -> InspectionError:
    if error.errno == errno.ENOENT:
        return InspectionError("NOT_FOUND", "Requested local path does not exist.")
    if error.errno in {errno.EACCES, errno.EPERM, errno.ELOOP, errno.ENOTDIR}:
        return InspectionError("ACCESS_DENIED", "Path access or handle verification was denied.")
    if error.errno in {errno.EMFILE, errno.ENFILE}:
        return InspectionError(
            "OUTPUT_LIMIT", "Path exceeds available handle verification capacity."
        )
    return InspectionError("UNAVAILABLE", "Local filesystem evidence is unavailable.")
