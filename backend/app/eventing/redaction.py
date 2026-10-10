"""Failure text that is safe to log, persist and show an operator (BIZ-249). Handlers' exceptions often embed credentials."""
from __future__ import annotations

import re

MAX_ERROR_CHARS = 300
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")
_SECRET_PAIR = re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|bearer)\b(\s*[=:]\s*|\s+)([^\s,;&'\"]+)")


def redact_error(exc_or_text: BaseException | str, *, secrets: tuple[str, ...] = ()) -> str:
    """`Type: message` with the given secret values, URL credentials and `key=value` secrets masked, and capped in length."""
    text = f"{type(exc_or_text).__name__}: {exc_or_text}" if isinstance(exc_or_text, BaseException) else str(exc_or_text)
    for s in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(s, "***")
    text = _URL_CREDENTIALS.sub(r"\1***@", text)
    text = _SECRET_PAIR.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    return text if len(text) <= MAX_ERROR_CHARS else text[:MAX_ERROR_CHARS - 1] + "…"
