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


@pytest.mark.parametrize("split", [1, 5, 17, 35, 60])
def test_stream_filter_handles_split_private_keys(split):
    cls = getattr(redaction, "RecordSanitizer", None)
    assert cls is not None, "stream filtering is missing"
    stream = cls(leading_fragment=False)
    text = (
        f"booting\n-----BEGIN PRIVATE KEY-----\n{FAKE}\n-----END PRIVATE KEY-----\nready\n".encode()
    )
    output = stream.feed(text[:split]) + stream.feed(text[split:]) + stream.finish()
    assert FAKE.encode() not in output
    assert b"ready" in output
    assert stream.redacted


def test_log_tail_without_private_key_header_is_withheld():
    cls = getattr(redaction, "RecordSanitizer", None)
    assert cls is not None, "log tail filtering is missing"
    stream = cls()
    output = stream.feed(b"YWJjZGVmZ2hpamtsbW5vcA==\n-----END PRIVATE KEY-----\nready now\n")
    output += stream.finish()
    assert b"YWJjZGVm" not in output
    assert b"ready now" in output


def test_incomplete_record_and_invalid_utf8_are_not_released():
    cls = getattr(redaction, "RecordSanitizer", None)
    assert cls is not None
    stream = cls(leading_fragment=False)
    output = stream.feed(b"visible\npassword=fixture-secret-") + stream.finish()
    assert b"fixture-secret" not in output
    assert stream.ambiguous
