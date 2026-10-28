"""Windows APIs are initialized only on native Windows."""

import ctypes
import os
from contextlib import ExitStack
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..models import InspectionError


def create_job(pid: int):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint64)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimit),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise OSError("Cannot create inspection job.")
    process = kernel.OpenProcess(0x0100 | 0x0001, False, pid)
    try:
        limits = ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise OSError("Cannot configure inspection job.")
        if not process or not kernel.AssignProcessToJobObject(job, process):
            raise OSError("Cannot contain inspection process.")
    except BaseException:
        kernel.CloseHandle(job)
        raise
    finally:
        if process:
            kernel.CloseHandle(process)
    return job


def close_job(job) -> None:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle(job)


class _UnicodeString(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("MaximumLength", ctypes.c_ushort),
        ("Buffer", ctypes.c_void_p),
    ]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ulong),
        ("RootDirectory", ctypes.c_void_p),
        ("ObjectName", ctypes.POINTER(_UnicodeString)),
        ("Attributes", ctypes.c_ulong),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    ]


class _IoStatus(ctypes.Structure):
    _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]


@lru_cache(maxsize=1)
def _file_api():
    from ctypes import wintypes as wt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native = ctypes.WinDLL("ntdll")
    signatures = {
        "CreateFileW": (
            [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE],
            wt.HANDLE,
        ),
        "CloseHandle": ([wt.HANDLE], wt.BOOL),
        "GetDriveTypeW": ([wt.LPCWSTR], wt.UINT),
        "GetFileType": ([wt.HANDLE], wt.DWORD),
        "GetFileInformationByHandleEx": (
            [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD],
            wt.BOOL,
        ),
        "GetFinalPathNameByHandleW": ([wt.HANDLE, wt.LPWSTR, wt.DWORD, wt.DWORD], wt.DWORD),
        "GetVolumeInformationByHandleW": (
            [
                wt.HANDLE,
                wt.LPWSTR,
                wt.DWORD,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                wt.LPWSTR,
                wt.DWORD,
            ],
            wt.BOOL,
        ),
    }
    for name, (args, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    native.NtCreateFile.argtypes = [
        ctypes.POINTER(wt.HANDLE),
        wt.DWORD,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatus),
        ctypes.c_void_p,
        wt.DWORD,
        wt.DWORD,
        wt.DWORD,
        wt.DWORD,
        ctypes.c_void_p,
        wt.DWORD,
    ]
    native.NtCreateFile.restype = ctypes.c_long
    native.NtQueryVolumeInformationFile.argtypes = [
        wt.HANDLE,
        ctypes.POINTER(_IoStatus),
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.c_int,
    ]
    native.NtQueryVolumeInformationFile.restype = ctypes.c_long
    native.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
    native.RtlNtStatusToDosError.restype = wt.DWORD
    return kernel, native


def _handle(fd):
    import msvcrt

    return msvcrt.get_osfhandle(fd)


def _fd(handle):
    import msvcrt

    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        _file_api()[0].CloseHandle(handle)
        raise


def _root(path):
    kernel, _ = _file_api()
    drive = str(path.anchor)
    if len(drive) != 3 or drive[1:] != ":\\" or kernel.GetDriveTypeW(drive) not in {2, 3, 5, 6}:
        raise InspectionError("UNSUPPORTED", "Only verified local drives are supported.")
    handle = kernel.CreateFileW(drive, 0x100080, 7, None, 3, 0x02300000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return _fd(handle)


def _relative(parent, name, *, data=False):
    from ctypes import wintypes as wt

    if not name or name in {".", ".."} or any(char in name for char in "\\/:\0"):
        raise InspectionError("INVALID_ARGUMENT", "Invalid local path component.")
    _, native = _file_api()
    encoded = name.encode("utf-16-le")
    buffer = ctypes.create_string_buffer(encoded + b"\0\0")
    text = _UnicodeString(len(encoded), len(encoded) + 2, ctypes.cast(buffer, ctypes.c_void_p))
    attributes = _ObjectAttributes(
        ctypes.sizeof(_ObjectAttributes), _handle(parent), ctypes.pointer(text), 0x40, None, None
    )
    handle, status = wt.HANDLE(), _IoStatus()
    result = native.NtCreateFile(
        ctypes.byref(handle),
        0x100080 | (1 if data else 0),
        ctypes.byref(attributes),
        ctypes.byref(status),
        None,
        0,
        7,
        1,
        0x00600020,
        None,
        0,
    )
    if result < 0:
        raise ctypes.WinError(native.RtlNtStatusToDosError(result))
    return _fd(handle.value)


def filesystem_is_local(fd):
    kernel, native = _file_api()
    handle = _handle(fd)
    if kernel.GetFileType(handle) != 1:
        return False
    device = (ctypes.c_ulong * 2)()
    status = _IoStatus()
    if (
        native.NtQueryVolumeInformationFile(
            handle, ctypes.byref(status), device, ctypes.sizeof(device), 4
        )
        < 0
    ):
        return False
    if device[1] & 0x10 or device[0] not in {2, 3, 7, 8, 0x24}:
        return False
    name = ctypes.create_unicode_buffer(32)
    return bool(
        kernel.GetVolumeInformationByHandleW(handle, None, 0, None, None, None, name, len(name))
    ) and name.value.casefold() in {"ntfs", "refs", "fat", "fat32", "exfat", "cdfs", "udf"}


def _attributes(fd):
    info = (ctypes.c_ulong * 2)()
    if not _file_api()[0].GetFileInformationByHandleEx(_handle(fd), 9, info, ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    attributes, tag = info
    if attributes & (0x1000 | 0x40000 | 0x400000):
        raise InspectionError(
            "UNSUPPORTED", "Cloud placeholders are not hydrated during inspection."
        )
    return attributes, tag


def _ordinary(fd):
    attributes, tag = _attributes(fd)
    if attributes & 0x400 and tag & ~0xF000 != 0x9000001A:
        raise InspectionError("UNSUPPORTED", "Unresolved reparse points are not read.")
    if not filesystem_is_local(fd):
        raise InspectionError("UNSUPPORTED", "Remote, virtual or unverified filesystem.")


def descriptor_path(fd):
    kernel, _ = _file_api()
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetFinalPathNameByHandleW(_handle(fd), buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        raise InspectionError("UNSUPPORTED", "Opened path cannot be verified.")
    value = buffer.value
    value = value.removeprefix("\\\\?\\")
    path = Path(value)
    if len(path.anchor) != 3 or path.anchor[1:] != ":\\":
        raise InspectionError("UNSUPPORTED", "Opened path is not on a local drive.")
    return path


def _identity(fd):
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def resolve_path(path, allow):
    aliases = []
    for _ in range(41):
        remaining = list(path.parts[1:])
        current = Path(path.anchor)
        changed = False
        with ExitStack() as stack:
            parent = _root(path)
            stack.callback(os.close, parent)
            _ordinary(parent)
            for index, name in enumerate(remaining):
                candidate = current / name
                allow(candidate)
                child = _relative(parent, name)
                stack.callback(os.close, child)
                attributes, tag = _attributes(child)
                if attributes & 0x400 and tag in {0xA0000003, 0xA000000C}:
                    # readlink reads reparse metadata only; retained handles and a
                    # second identity check reject concurrent alias replacement.
                    identity = _identity(child)
                    target = os.readlink(candidate)
                    with ExitStack() as verification:
                        again = _relative(parent, name)
                        verification.callback(os.close, again)
                        if _identity(again) != identity:
                            raise InspectionError(
                                "ACCESS_DENIED", "Alias changed during resolution."
                            )
                    aliases.append((candidate, identity, target))
                    target = target.removeprefix("\\\\?\\")
                    target_path = Path(target)
                    full = target_path if target_path.is_absolute() else current / target_path
                    full = Path(os.path.normpath(str(full / Path(*remaining[index + 1 :]))))
                    allow(full)
                    path = full
                    changed = True
                    break
                _ordinary(child)
                if index < len(remaining) - 1 and not attributes & 0x10:
                    raise InspectionError("ACCESS_DENIED", "Path ancestry is not a directory.")
                current, parent = candidate, child
        if not changed:
            return path, aliases
    raise InspectionError("ACCESS_DENIED", "Alias resolution is ambiguous.")


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

    def verify(self):
        for parent, name, child in self.trace:
            other = _relative(parent, name)
            try:
                if _identity(other) != _identity(child):
                    raise InspectionError("ACCESS_DENIED", "Path changed during inspection.")
                _ordinary(other)
            finally:
                os.close(other)
        for path, identity, target in self.aliases:
            # Resolve ancestors without following aliases into data reads.
            parent_path, _ = resolve_path(path.parent, self.allow)
            with ExitStack() as stack:
                parent = _root(parent_path)
                stack.callback(os.close, parent)
                for name in parent_path.parts[1:]:
                    parent = _relative(parent, name)
                    stack.callback(os.close, parent)
                    _ordinary(parent)
                other = _relative(parent, path.name)
                stack.callback(os.close, other)
                if _identity(other) != identity or os.readlink(path) != target:
                    raise InspectionError("ACCESS_DENIED", "Alias changed during inspection.")
        _ordinary(self.fd)
        self.allow(descriptor_path(self.fd))
        actual = os.fstat(self.fd)
        if not self.directory and (actual.st_size, actual.st_mtime_ns, actual.st_ctime_ns) != (
            self.initial.st_size,
            self.initial.st_mtime_ns,
            self.initial.st_ctime_ns,
        ):
            raise InspectionError("ACCESS_DENIED", "Source changed during inspection.")

    def close(self):
        self.stack.close()


def open_verified(path, directory, allow, aliases, ctx):
    stack = ExitStack()
    trace = []
    try:
        parent = _root(path)
        stack.callback(os.close, parent)
        _ordinary(parent)
        fd = parent
        for index, name in enumerate(path.parts[1:]):
            is_directory = index < len(path.parts) - 2 or directory
            metadata = _relative(parent, name)
            stack.callback(os.close, metadata)
            _ordinary(metadata)
            attributes, _ = _attributes(metadata)
            if bool(attributes & 0x10) != is_directory:
                raise InspectionError(
                    "UNSUPPORTED", "Only ordinary files and directories are supported."
                )
            allow(descriptor_path(metadata))
            fd = metadata if is_directory else _relative(parent, name, data=True)
            if fd != metadata:
                stack.callback(os.close, fd)
                if _identity(fd) != _identity(metadata):
                    raise InspectionError("ACCESS_DENIED", "Source changed while opening.")
            _ordinary(fd)
            trace.append((parent, name, fd))
            parent = fd
        handle = VerifiedHandle(path, fd, os.fstat(fd), directory, stack, trace, aliases, allow)
        handle.verify()
        return handle
    except BaseException:
        stack.close()
        raise
