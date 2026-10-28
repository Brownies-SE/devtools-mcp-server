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
