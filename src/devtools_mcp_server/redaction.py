import re
from dataclasses import dataclass

from .models import InspectionError

REDACTED = "[REDACTED]"
KEY_NAME = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|token|credential|credentials|"
    r"(?:api|access|refresh|auth|private|signing|client)[_-]?(?:key|token|secret))"
)
_PRIVATE = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----.*?"
    r"(?:-----END (?:[A-Z0-9]+ )?PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
_HEADERS = re.compile(
    r"""(?im)(["']?(?:authorization|cookie|set-cookie)["']?\s*[:=]\s*)([^\r\n]+)"""
)
_ASSIGNMENT = re.compile(
    r"""(?i)(["']?(?:password|passwd|pwd|secret|token|credential|credentials|"""
    r"""(?:api|access|refresh|auth|private|signing|client)[_-]?(?:key|token|secret))"""
    r"""["']?\s*[:=]\s*)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\r\n]+)"""
)
_URL = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/\s@]+@")
_TOKENS = re.compile(
    r"\b(?:AKIA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b"
)
_OPAQUE_LINE = re.compile(r"[A-Za-z0-9+/=]{16,}")


@dataclass(frozen=True)
class SanitizedText:
    text: str
    redacted: bool = False
    ambiguous: bool = False


def sanitize_text(text: str) -> SanitizedText:
    original = text
    ambiguous = text.count("BEGIN ") > text.count("END ") and "PRIVATE KEY" in text
    text = _PRIVATE.sub(lambda match: re.sub(r"[^\r\n]+", REDACTED, match.group()), text)
    text = _HEADERS.sub(lambda match: match[1] + REDACTED, text)
    text = _ASSIGNMENT.sub(lambda match: match[1] + REDACTED, text)
    text = _URL.sub(lambda match: match[1] + REDACTED + "@", text)
    text = _TOKENS.sub(REDACTED, text)
    return SanitizedText(text, text != original, ambiguous)


class RecordSanitizer:
    def __init__(self, leading_fragment: bool = True):
        self._leading = leading_fragment
        self._private = False
        self._buffer = b""
        self.redacted = False
        self.ambiguous = False
        self.omitted_count = 0

    def feed(self, chunk: bytes) -> bytes:
        if len(self._buffer) + len(chunk) > 1048576:
            raise InspectionError("OUTPUT_LIMIT", "Log record exceeds the capture limit.")
        self._buffer += chunk
        output = []
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                text = line.decode("utf-8")
            except UnicodeDecodeError:
                self.ambiguous = True
                self.omitted_count += 1
                continue
            if "-----BEGIN " in text and "PRIVATE KEY-----" in text:
                self._private = True
            if self._private:
                output.append((REDACTED + "\n").encode())
                self.redacted = True
                if "-----END " in text and "PRIVATE KEY-----" in text:
                    self._private = False
                    self._leading = False
                continue
            if "-----END " in text and "PRIVATE KEY-----" in text:
                self.redacted = True
                self._leading = False
                self.omitted_count += 1
                continue
            if self._leading and _OPAQUE_LINE.fullmatch(text.strip()):
                self.redacted = True
                self.ambiguous = True
                self.omitted_count += 1
                continue
            self._leading = False
            clean = sanitize_text(text)
            self.redacted |= clean.redacted
            output.append((clean.text + "\n").encode())
        return b"".join(output)

    def finish(self) -> bytes:
        if self._buffer or self._private:
            self.ambiguous = True
            self.omitted_count += 1
        self._buffer = b""
        return b""
