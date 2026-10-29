import os
import subprocess

import pytest

from devtools_mcp_server.models import InspectionError, StartupConfig
from devtools_mcp_server.paths import PathPolicy

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows handles required")


@pytest.mark.parametrize("name", ["app.py", "spacé name.py"])
def test_materialized_local_file(tmp_path, inspection_context, name):
    source = tmp_path / name
    source.write_text("print('hello')", encoding="utf-8")
    with PathPolicy(StartupConfig()).open_text(str(source), inspection_context) as opened:
        assert opened.read_utf8() == "print('hello')"


@pytest.mark.parametrize(
    "path",
    [
        r"\\server\share\app.py",
        r"\\.\PhysicalDrive0",
        r"\\?\GLOBALROOT\Device\HarddiskVolume1\app.py",
    ],
)
def test_device_and_network_paths_denied(path, inspection_context):
    with pytest.raises(InspectionError):
        PathPolicy(StartupConfig()).open_text(path, inspection_context)


def test_case_insensitive_secret_and_alternate_stream(tmp_path, inspection_context):
    secret = tmp_path / ".ENV.PRODUCTION"
    secret.write_text("secret")
    source = tmp_path / "app.py"
    source.write_text("safe")
    with open(str(source) + ":private", "w") as stream:
        stream.write("hidden")
    for path in [str(secret), str(source) + ":private"]:
        with pytest.raises(InspectionError):
            PathPolicy(StartupConfig()).open_text(path, inspection_context)


def junction(link, target):
    subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
    )


def test_safe_junction_and_secret_target(tmp_path, inspection_context):
    normal = tmp_path / "project with spaces"
    normal.mkdir()
    (normal / "app.py").write_text("safe")
    alias = tmp_path / "alias"
    junction(alias, normal)
    with PathPolicy(StartupConfig()).open_text(str(alias / "app.py"), inspection_context) as opened:
        assert opened.read_utf8() == "safe"
    secret = tmp_path / ".aws"
    secret.mkdir()
    (secret / "credentials").write_text("hidden")
    hidden = tmp_path / "hidden"
    junction(hidden, secret)
    with pytest.raises(InspectionError) as error:
        PathPolicy(StartupConfig()).open_text(str(hidden / "credentials"), inspection_context)
    assert error.value.code == "SECRET_BLOCKED"


def test_unknown_locality_denied(tmp_path, inspection_context, monkeypatch):
    from devtools_mcp_server.platforms import windows

    source = tmp_path / "app.py"
    source.write_text("safe")
    assert callable(getattr(windows, "filesystem_is_local", None)), (
        "Native volume verification missing"
    )
    monkeypatch.setattr(windows, "filesystem_is_local", lambda fd: False)
    with pytest.raises(InspectionError) as error:
        PathPolicy(StartupConfig()).open_text(str(source), inspection_context)
    assert error.value.code == "UNSUPPORTED"


def test_junction_replacement_before_read_is_denied(tmp_path, inspection_context, monkeypatch):
    safe = tmp_path / "safe"
    safe.mkdir()
    (safe / "app.py").write_text("safe")
    other = tmp_path / "other"
    other.mkdir()
    (other / "app.py").write_text("replacement must not be read")
    alias = tmp_path / "alias"
    junction(alias, safe)
    with PathPolicy(StartupConfig()).open_text(str(alias / "app.py"), inspection_context) as opened:
        os.rmdir(alias)
        junction(alias, other)

        def unexpected_read(*args):
            raise AssertionError("Source read before alias revalidation")

        monkeypatch.setattr(os, "read", unexpected_read)
        with pytest.raises(InspectionError) as error:
            opened.read_utf8()
        assert error.value.code == "ACCESS_DENIED"


def test_offline_placeholder_rejected_before_read(tmp_path, inspection_context, monkeypatch):
    import ctypes
    from ctypes import wintypes

    source = tmp_path / "offline.py"
    source.write_text("must not be hydrated")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel.SetFileAttributesW
    function.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    function.restype = wintypes.BOOL
    assert function(str(source), 0x1000)
    try:

        def unexpected_read(*args):
            raise AssertionError("Placeholder data read")

        monkeypatch.setattr(os, "read", unexpected_read)
        with pytest.raises(InspectionError) as error:
            PathPolicy(StartupConfig()).open_text(str(source), inspection_context)
        assert error.value.code == "UNSUPPORTED"
    finally:
        assert function(str(source), 0x80)


def test_mapped_remote_drive_denied_before_open(tmp_path, inspection_context, monkeypatch):
    from devtools_mcp_server.platforms import windows

    source = tmp_path / "app.py"
    source.write_text("safe")
    kernel, _ = windows._file_api()
    monkeypatch.setattr(kernel, "GetDriveTypeW", lambda path: 4)
    with pytest.raises(InspectionError) as error:
        PathPolicy(StartupConfig()).open_text(str(source), inspection_context)
    assert error.value.code == "UNSUPPORTED"


def test_directory_listing_uses_verified_handle(tmp_path, inspection_context, monkeypatch):
    directory = tmp_path / "project"
    (directory / "nested").mkdir(parents=True)
    (directory / "nested" / "app.py").write_text("safe")

    def raw_directory_reopen(*args, **kwargs):
        raise AssertionError("Directory enumeration reopened an unverified pathname")

    with monkeypatch.context() as context:
        context.setattr(os, "scandir", raw_directory_reopen)
        entries = list(
            PathPolicy(StartupConfig()).iter_entries(str(directory), True, inspection_context)
        )
    assert "nested/app.py" in [entry.relative_path for entry in entries]
