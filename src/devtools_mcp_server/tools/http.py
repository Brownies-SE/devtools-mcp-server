import http.client
import io
import ipaddress
import re
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from ..models import InspectionError, Reply, StartupConfig, StrictModel, Warning
from ..redaction import sanitize_text

HTTP_TIMEOUT = 5.0
_HEALTH_PATHS = {"/", "/health", "/healthz", "/ready", "/readyz", "/live", "/livez", "/status"}


@dataclass(frozen=True)
class ValidatedHealthTarget:
    scheme: str
    hostname: str
    address: str
    port: int
    path: str


class HttpCheckArgs(StrictModel):
    url: str = Field(min_length=1, max_length=4096)
    method: Literal["HEAD", "GET"] = "HEAD"
    include_body: bool = False

    @model_validator(mode="after")
    def body_requires_get(self):
        if self.include_body and self.method != "GET":
            raise ValueError("Body collection requires GET.")
        return self


class HttpCheckData(StrictModel):
    http_status: int
    duration_seconds: float
    headers: dict[str, str]
    body: str | None = None


def validate_health_url(url: str, config: StartupConfig) -> ValidatedHealthTarget:
    try:
        if (
            not url
            or len(url) > 4096
            or any(char.isspace() or ord(char) < 32 for char in url)
            or any(char in url for char in "\\%?#@")
        ):
            raise ValueError
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError
        authority = parsed.netloc
        if authority.startswith("["):
            match = re.fullmatch(r"\[(::1)\](?::([0-9]+))?", authority)
            if match is None:
                raise ValueError
            hostname, port_text = match.groups()
            address = "::1"
        else:
            match = re.fullmatch(r"([^:]+)(?::([0-9]+))?", authority)
            if match is None:
                raise ValueError
            hostname, port_text = match.groups()
            if hostname == "localhost":
                address = "127.0.0.1"
            else:
                literal = ipaddress.IPv4Address(hostname)
                if not literal.is_loopback or str(literal) != hostname:
                    raise ValueError
                address = str(literal)
        port = (
            int(port_text) if port_text is not None else (443 if parsed.scheme == "https" else 80)
        )
        if not 1 <= port <= 65535 or port_text is not None and str(port) != port_text:
            raise ValueError
        path = parsed.path or "/"
        if path not in _HEALTH_PATHS | set(config.extra_health_paths):
            raise ValueError
        return ValidatedHealthTarget(parsed.scheme, hostname, address, port, path)
    except ValueError:
        raise InspectionError(
            "INVALID_ARGUMENT",
            "Only canonical loopback URLs with approved health paths are allowed.",
        ) from None


def _remaining(deadline):
    duration = deadline - time.monotonic()
    if duration <= 0:
        raise InspectionError("TIMEOUT", "Health check exceeded its total deadline.")
    return duration


class _PinnedConnection(http.client.HTTPConnection):
    def __init__(self, target, deadline):
        super().__init__(target.hostname, target.port, timeout=_remaining(deadline))
        self.target, self.deadline = target, deadline

    def connect(self):
        target = self.target
        sock = socket.socket(
            socket.AF_INET6 if target.address == "::1" else socket.AF_INET, socket.SOCK_STREAM
        )
        try:
            sock.settimeout(_remaining(self.deadline))
            sock.connect((target.address, target.port))
            if target.scheme == "https":
                sock.settimeout(_remaining(self.deadline))
                sock = ssl.create_default_context().wrap_socket(
                    sock, server_hostname=target.hostname
                )
            sock.settimeout(_remaining(self.deadline))
            self.sock = sock
        except BaseException:
            sock.close()
            raise


class _WireReader(io.RawIOBase):
    def __init__(self, sock, deadline):
        super().__init__()
        self.sock, self.deadline = sock, deadline
        self.header_mode = True
        self.header_bytes = self.body_bytes = 0

    def readable(self):
        return True

    def _receive(self, size):
        maximum = 16384 if self.header_mode else 1048576
        count = self.header_bytes if self.header_mode else self.body_bytes
        if count >= maximum:
            raise InspectionError("OUTPUT_LIMIT", "HTTP wire capture limit reached.")
        size = min(size if size > 0 else 16384, maximum - count)
        self.sock.settimeout(_remaining(self.deadline))
        data = self.sock.recv(size)
        if self.header_mode:
            self.header_bytes += len(data)
        else:
            self.body_bytes += len(data)
        return data

    def read(self, size=-1):
        if size == 0:
            return b""
        wanted = size if size >= 0 else 16384
        chunks = bytearray()
        while len(chunks) < wanted:
            data = self._receive(wanted - len(chunks))
            if not data:
                break
            chunks.extend(data)
        return bytes(chunks)

    def readline(self, size=-1):
        # No buffered read-ahead: header-only requests never collect body bytes.
        output = bytearray()
        while size < 0 or len(output) < size:
            chunk = self.read(1)
            if not chunk:
                break
            output.extend(chunk)
            if chunk == b"\n":
                break
        return bytes(output)


class _ResponseSocket:
    def __init__(self, reader):
        self.reader = reader

    def makefile(self, *args, **kwargs):
        return self.reader


def http_check(args: HttpCheckArgs, ctx) -> Reply[HttpCheckData]:
    started = time.monotonic()
    deadline = min(ctx.deadline, started + HTTP_TIMEOUT)
    target = validate_health_url(args.url, ctx.config)
    connection = _PinnedConnection(target, deadline)
    response = None
    warnings, body, redacted = [], None, False
    try:
        connection.request(
            args.method, target.path, headers={"Accept-Encoding": "identity", "Connection": "close"}
        )
        reader = _WireReader(connection.sock, deadline)
        response = http.client.HTTPResponse(_ResponseSocket(reader), method=args.method)
        response.begin()
        reader.header_mode = False
        headers = {
            name: response.getheader(name)
            for name in ("content-type", "content-length", "allow")
            if response.getheader(name) is not None
        }
        if args.include_body:
            encoding = response.getheader("content-encoding", "identity").strip().casefold()
            media_type = response.getheader("content-type", "").split(";", 1)[0].strip().casefold()
            if encoding != "identity" or not (
                media_type.startswith("text/") or media_type == "application/json"
            ):
                warnings.append(
                    Warning(
                        code="UNSUPPORTED",
                        message="Encoded or non-text response bodies are omitted.",
                    )
                )
            elif response.length is not None and response.length > 1048576:
                warnings.append(
                    Warning(code="OUTPUT_LIMIT", message="Response body exceeds the wire limit.")
                )
            else:
                content = bytearray()
                try:
                    while chunk := response.read(16384):
                        content.extend(chunk)
                except InspectionError as error:
                    if error.code != "OUTPUT_LIMIT":
                        raise
                    warnings.append(
                        Warning(
                            code=error.code,
                            message="Response body exceeded the wire limit and was omitted.",
                        )
                    )
                if response.length not in (None, 0):
                    warnings.append(
                        Warning(code="UNAVAILABLE", message="Incomplete response body was omitted.")
                    )
                if not warnings:
                    try:
                        if b"\0" in content:
                            raise UnicodeDecodeError("utf-8", b"\0", 0, 1, "binary body")
                        text = content.decode("utf-8-sig")
                        clean = sanitize_text(text)
                        body, redacted = clean.text, clean.redacted
                    except UnicodeDecodeError:
                        warnings.append(
                            Warning(
                                code="UNSUPPORTED",
                                message="Only valid UTF-8 text bodies are returned.",
                            )
                        )
        _remaining(deadline)
        return Reply[HttpCheckData](
            data=HttpCheckData(
                http_status=response.status,
                duration_seconds=time.monotonic() - started,
                headers=headers,
                body=body,
            ),
            status="partial" if warnings else "ok",
            warnings=warnings,
            redacted=redacted,
            truncated=any(item.code == "OUTPUT_LIMIT" for item in warnings),
        )
    except TimeoutError:
        raise InspectionError("TIMEOUT", "Health check exceeded its total deadline.") from None
    except PermissionError:
        raise InspectionError(
            "ACCESS_DENIED", "The OS denied the local health connection."
        ) from None
    except OSError, http.client.HTTPException:
        raise InspectionError(
            "UNAVAILABLE", "Local health connection, TLS verification or HTTP parsing failed."
        ) from None
    finally:
        if response is not None:
            response.close()
        connection.close()
