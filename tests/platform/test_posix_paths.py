import os
import time

import psutil
import pytest

from devtools_mcp_server import paths
from devtools_mcp_server.models import InspectionError, StartupConfig
from devtools_mcp_server.platforms import posix

pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX handle checks")


def policy():
    cls = getattr(paths, "PathPolicy", None)
    assert cls is not None, "POSIX verified-handle policy is missing"
    return cls(StartupConfig())


def test_direct_alias_cannot_read_secret_target(tmp_path, inspection_context):
    secret = tmp_path / ".env"
    secret.write_text("fixture-secret")
    alias = tmp_path / "ordinary.py"
    alias.symlink_to(secret)
    with pytest.raises(InspectionError):
        policy().open_text(str(alias), inspection_context)


def test_direct_alias_to_ordinary_file_is_supported(tmp_path, inspection_context):
    source = tmp_path / "app.py"
    source.write_text("safe text")
    alias = tmp_path / "alias.py"
    alias.symlink_to(source)
    with policy().open_text(str(alias), inspection_context) as opened:
        assert opened.read_utf8() == "safe text"


def test_fifo_is_rejected_without_blocking(tmp_path, inspection_context):
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    begin = time.monotonic()
    with pytest.raises(InspectionError):
        policy().open_text(str(fifo), inspection_context)
    assert time.monotonic() - begin < 1


def test_unverified_filesystem_never_releases_content(monkeypatch, tmp_path, inspection_context):
    reader = policy()
    source = tmp_path / "app.py"
    source.write_text("fixture-secret")
    monkeypatch.setattr(posix, "filesystem_is_local", lambda fd: False)
    with pytest.raises(InspectionError) as caught:
        reader.open_text(str(source), inspection_context)
    assert caught.value.code == "UNSUPPORTED"


def test_replacement_between_validation_and_open_is_denied(
    monkeypatch, tmp_path, inspection_context
):
    reader = policy()
    source = tmp_path / "app.py"
    source.write_text("safe text")
    secret = tmp_path / ".env"
    secret.write_text("fixture-secret")
    original = os.open
    changed = False

    def replace_before_open(name, flags, mode=0o777, *, dir_fd=None):
        nonlocal changed
        if str(name) == "app.py" and not flags & os.O_DIRECTORY and not changed:
            changed = True
            source.unlink()
            source.symlink_to(secret)
        return original(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(posix.os, "open", replace_before_open)
    with (
        pytest.raises(InspectionError),
        reader.open_text(str(source), inspection_context) as opened,
    ):
        opened.read_utf8()
    assert changed


def test_repeated_safe_reads_close_all_handles(tmp_path, inspection_context):
    reader = policy()
    source = tmp_path / "app.py"
    source.write_text("safe")
    process = psutil.Process()
    with reader.open_text(str(source), inspection_context) as opened:
        assert opened.read_utf8() == "safe"
    before = process.num_fds()
    for _ in range(30):
        with reader.open_text(str(source), inspection_context) as opened:
            assert opened.read_utf8() == "safe"
    assert process.num_fds() == before


def test_cloud_placeholder_is_denied_before_content_open(monkeypatch, tmp_path, inspection_context):
    reader = policy()
    source = tmp_path / "cloud.txt"
    source.write_text("fixture-secret")
    real_stat = os.stat
    real_open = os.open
    opened_source = []

    class CloudStat:
        st_flags = 0x40000000

        def __init__(self, original):
            self.original = original

        def __getattr__(self, name):
            return getattr(self.original, name)

    def cloud_stat(name, *args, **kwargs):
        original = real_stat(name, *args, **kwargs)
        return CloudStat(original) if str(name) == "cloud.txt" else original

    def observe_open(name, flags, *args, **kwargs):
        if str(name) == "cloud.txt":
            opened_source.append(name)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(posix.os, "stat", cloud_stat)
    monkeypatch.setattr(posix.os, "open", observe_open)
    with pytest.raises(InspectionError) as caught:
        reader.open_text(str(source), inspection_context)
    assert caught.value.code == "UNSUPPORTED"
    assert not opened_source


def test_opened_handle_must_still_be_regular(monkeypatch, tmp_path, inspection_context):
    import stat

    reader = policy()
    source = tmp_path / "type-changed.txt"
    os.mkfifo(source)
    real_stat = os.stat

    class BeforeReplacement:
        st_mode = stat.S_IFREG | 0o600

        def __init__(self, original):
            self.original = original

        def __getattr__(self, name):
            return getattr(self.original, name)

    def prior_stat(name, *args, **kwargs):
        original = real_stat(name, *args, **kwargs)
        return BeforeReplacement(original) if str(name) == "type-changed.txt" else original

    monkeypatch.setattr(posix.os, "stat", prior_stat)
    with (
        pytest.raises(InspectionError),
        reader.open_text(str(source), inspection_context) as opened,
    ):
        opened.read_utf8()
