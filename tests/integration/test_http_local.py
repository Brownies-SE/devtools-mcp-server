import importlib
import socket
import ssl
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address

import pytest

from devtools_mcp_server.models import InspectionError


def module():
    assert importlib.util.find_spec("devtools_mcp_server.tools.http"), (
        "Bounded loopback health inspector missing"
    )
    return importlib.import_module("devtools_mcp_server.tools.http")


@contextmanager
def local_server(response, *, context=None, drip=None, hold_body=False):
    stop = threading.Event()
    received = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(2)
        port = listener.getsockname()[1]

        def serve():
            try:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(2)
                    if context is not None:
                        connection = context.wrap_socket(connection, server_side=True)
                    with connection:
                        request = b""
                        while b"\r\n\r\n" not in request and len(request) < 8192:
                            piece = connection.recv(1024)
                            if not piece:
                                return
                            request += piece
                        received.append(request)
                        if drip:
                            for byte in response:
                                if stop.wait(drip):
                                    return
                                connection.sendall(bytes([byte]))
                        else:
                            connection.sendall(response)
                        if hold_body:
                            stop.wait(2)
            except OSError:
                pass

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield f"{'https' if context else 'http'}://localhost:{port}/health", received
        finally:
            stop.set()
            thread.join(timeout=3)
            assert not thread.is_alive(), "Owned HTTP fixture did not exit"


def response(body=b"ok", status=200, extra=b""):
    return (
        f"HTTP/1.1 {status} Fixture\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\n".encode()
        + extra
        + b"\r\n"
        + body
    )


def test_no_dns_or_proxy_and_no_credentials_returned(inspection_context, monkeypatch):
    m = module()
    with local_server(
        response(
            b"password=fixture-secret\nvisible",
            500,
            b"Set-Cookie: secret=cookie\r\nAuthorization: Bearer fixture\r\n",
        )
    ) as (url, received):

        def no_dns(*args, **kwargs):
            raise AssertionError("DNS must never be used")

        monkeypatch.setattr(socket, "getaddrinfo", no_dns)
        monkeypatch.setenv("HTTP_PROXY", "http://example.invalid:9999")
        reply = m.http_check(
            m.HttpCheckArgs(url=url, method="GET", include_body=True), inspection_context
        )
    assert reply.status == "ok"
    assert reply.data.http_status == 500
    assert "visible" in reply.data.body
    assert reply.redacted
    assert "fixture-secret" not in reply.model_dump_json()
    assert set(reply.data.headers) == {"content-type", "content-length"}
    assert b"Accept-Encoding: identity" in received[0]


@pytest.mark.parametrize("method", ["HEAD", "GET"])
def test_headers_only_returns_without_waiting_for_body(method, inspection_context):
    m = module()
    raw = b"HTTP/1.1 200 OK\r\nContent-Length: 999999999\r\n\r\n"
    with local_server(raw, hold_body=True) as (url, _):
        started = time.monotonic()
        reply = m.http_check(m.HttpCheckArgs(url=url, method=method), inspection_context)
        assert time.monotonic() - started < 1.0
    assert reply.data.body is None
    assert reply.data.http_status == 200


def test_redirect_is_observed_without_following(inspection_context):
    m = module()
    with local_server(response(b"", 302, b"Location: http://example.invalid/\r\n")) as (
        url,
        received,
    ):
        reply = m.http_check(m.HttpCheckArgs(url=url), inspection_context)
    assert reply.data.http_status == 302
    assert len(received) == 1
    assert "location" not in reply.data.headers


def test_header_budget_is_enforced_before_parser_buffering(inspection_context):
    m = module()
    with (
        local_server(b"HTTP/1.1 200 OK\r\nX-Large: " + b"x" * 16384 + b"\r\n\r\n") as (url, _),
        pytest.raises(InspectionError) as error,
    ):
        m.http_check(m.HttpCheckArgs(url=url), inspection_context)
    assert error.value.code == "OUTPUT_LIMIT"


@pytest.mark.parametrize(
    "body,extra", [(b"\xff", b""), (b"binary\0", b""), (b"encoded", b"Content-Encoding: gzip\r\n")]
)
def test_unsupported_body_is_omitted_with_partial_evidence(body, extra, inspection_context):
    m = module()
    with local_server(response(body, extra=extra)) as (url, _):
        reply = m.http_check(
            m.HttpCheckArgs(url=url, method="GET", include_body=True), inspection_context
        )
    assert reply.status == "partial"
    assert reply.data.body is None
    assert reply.warnings[0].code == "UNSUPPORTED"


def test_body_wire_cap_returns_partial_headers(inspection_context):
    m = module()
    raw = (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\n\r\n"
        + b"100001\r\n"
        + b"x" * 1048577
        + b"\r\n0\r\n\r\n"
    )
    with local_server(raw) as (url, _):
        reply = m.http_check(
            m.HttpCheckArgs(url=url, method="GET", include_body=True), inspection_context
        )
    assert reply.status == "partial"
    assert reply.data.body is None
    assert any(warning.code == "OUTPUT_LIMIT" for warning in reply.warnings)


def test_slow_drip_uses_one_total_deadline(inspection_context, monkeypatch):
    m = module()
    monkeypatch.setattr(m, "HTTP_TIMEOUT", 0.2)
    with local_server(response(), drip=0.05) as (url, _):
        started = time.monotonic()
        with pytest.raises(InspectionError) as error:
            m.http_check(m.HttpCheckArgs(url=url), inspection_context)
        assert time.monotonic() - started < 0.8
    assert error.value.code == "TIMEOUT"


@pytest.fixture
def tls_contexts(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "fixture.pem", tmp_path / "fixture.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(str(cert_path), str(key_path))
    trusted = ssl.create_default_context(cafile=str(cert_path))
    return server, trusted


def test_tls_uses_original_hostname_and_verifies_certificate(
    tls_contexts, inspection_context, monkeypatch
):
    m = module()
    server, trusted = tls_contexts
    monkeypatch.setattr(m.ssl, "create_default_context", lambda: trusted)
    with local_server(response(), context=server) as (url, _):
        reply = m.http_check(
            m.HttpCheckArgs(url=url, method="GET", include_body=True), inspection_context
        )
    assert reply.data.body == "ok"


def test_untrusted_tls_certificate_is_rejected(tls_contexts, inspection_context):
    m = module()
    server, _ = tls_contexts
    with (
        local_server(response(), context=server) as (url, _),
        pytest.raises(InspectionError) as error,
    ):
        m.http_check(m.HttpCheckArgs(url=url), inspection_context)
    assert error.value.code == "UNAVAILABLE"
