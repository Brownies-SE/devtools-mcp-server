import re
from dataclasses import dataclass

REDACTED = "[REDACTED]"
KEY_NAME = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|token|credential|credentials|"
    r"(?:api|access|refresh|auth|private|signing|client)[_-]?(?:key|token|secret))"
)
_PRIVATE = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY(?: BLOCK)?-----.*?"
    r"(?:-----END (?:[A-Z0-9]+ )?PRIVATE KEY(?: BLOCK)?-----|\Z)",
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
