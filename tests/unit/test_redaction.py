import pytest

from devtools_mcp_server import redaction

FAKE = "fixture-secret-12345"


def sanitize(text):
    function = getattr(redaction, "sanitize_text", None)
    assert callable(function), "shared secret filter is missing"
    return function(text)


@pytest.mark.parametrize(
    "text",
    [
        f"password={FAKE}",
        f'{{"api_key": "{FAKE}", "safe": "visible"}}',
        f"Authorization: Bearer {FAKE}",
        f"Cookie: session={FAKE}; other=abc",
        f"https://developer:{FAKE}@localhost:8000",
        f"-----BEGIN PRIVATE KEY-----\n{FAKE}\n-----END PRIVATE KEY-----\n",
        f"-----BEGIN OPENSSH PRIVATE KEY-----\n{FAKE}\n",
    ],
)
def test_secret_forms_are_removed(text):
    result = sanitize(text)
    assert FAKE not in result.text
    assert result.redacted


def test_redaction_preserves_source_line_count():
    source = "start\n-----BEGIN PRIVATE KEY-----\naaa\nbbb\n-----END PRIVATE KEY-----\nend\n"
    result = sanitize(source)
    assert len(result.text.splitlines()) == 6
    assert result.text.splitlines()[-1] == "end"
    assert "aaa" not in result.text


def test_ordinary_text_is_preserved():
    assert sanitize("port=8000\nname=api\n").text == "port=8000\nname=api\n"


def test_pgp_private_key_armor_is_filtered():
    clean = sanitize(
        "-----BEGIN PGP PRIVATE KEY BLOCK-----\nfixture-pgp-body\n-----END PGP PRIVATE KEY BLOCK-----\n"
    )
    assert "fixture-pgp-body" not in clean.text
    assert clean.redacted
