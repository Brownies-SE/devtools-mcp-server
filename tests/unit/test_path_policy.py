import pytest

from devtools_mcp_server import paths
from devtools_mcp_server.models import InspectionError, StartupConfig


def policy(config=None):
    cls = getattr(paths, "PathPolicy", None)
    assert cls is not None, "verified source policy is missing"
    return cls(config or StartupConfig())


@pytest.mark.parametrize(
    "relative",
    [
        ".env",
        ".env.local",
        ".env.example",
        ".ssh/id_ed25519",
        ".aws/credentials",
        ".azure/accessTokens.json",
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".git-credentials",
        ".docker/config.json",
        ".kube/config",
        ".config/gcloud/application_default_credentials.json",
        "key.pem",
        "private.key",
        ".git/config",
    ],
)
def test_known_secret_sources_are_denied_before_read(relative, tmp_path, inspection_context):
    source = tmp_path / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("fixture-secret-12345")
    with pytest.raises(InspectionError) as caught:
        policy().open_text(str(source), inspection_context)
    assert caught.value.code == "SECRET_BLOCKED"


@pytest.mark.parametrize("name", ["package.json", "tsconfig.json", ".environment.py", "app.py"])
def test_ordinary_sources_remain_readable(name, tmp_path, inspection_context):
    source = tmp_path / name
    source.write_text("ordinary development text\n")
    with policy().open_text(str(source), inspection_context) as opened:
        assert opened.read_utf8() == "ordinary development text\n"


def test_additional_operator_exclusion_cannot_be_bypassed(tmp_path, inspection_context):
    source = tmp_path / "private-settings.json"
    source.write_text("fixture-secret")
    with pytest.raises(InspectionError) as caught:
        policy(StartupConfig(extra_secret_paths=(str(source),))).open_text(
            str(source), inspection_context
        )
    assert caught.value.code == "SECRET_BLOCKED"


def test_binary_and_oversized_sources_are_not_returned(tmp_path, inspection_context):
    reader = policy()
    binary = tmp_path / "binary.dat"
    binary.write_bytes(b"\x00binary")
    with reader.open_text(str(binary), inspection_context) as opened:
        with pytest.raises(InspectionError) as caught:
            opened.read_utf8()
        assert caught.value.code == "UNSUPPORTED"
    large = tmp_path / "large.txt"
    large.write_bytes(b"x" * 1048577)
    with (
        pytest.raises(InspectionError) as caught,
        reader.open_text(str(large), inspection_context) as opened,
    ):
        opened.read_utf8()
    assert caught.value.code == "OUTPUT_LIMIT"


def test_operator_exclusion_is_canonicalized_across_aliases(tmp_path, inspection_context):
    source = tmp_path / "private-settings.json"
    source.write_text("fixture-secret")
    alias = tmp_path / "excluded-alias"
    try:
        alias.symlink_to(source)
    except OSError, NotImplementedError:
        pytest.skip("symlink creation is unavailable")
    reader = policy(StartupConfig(extra_secret_paths=(str(alias),)))
    with pytest.raises(InspectionError) as caught:
        reader.open_text(str(source), inspection_context)
    assert caught.value.code == "SECRET_BLOCKED"
