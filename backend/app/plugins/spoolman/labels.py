"""Spoolman spool labels: `web+spoolman:s-<id>`, also `s-<id>`, a `/spool/show/<id>` URL, or a bare number."""
from __future__ import annotations

import re

_PATTERNS = (
    re.compile(r"web\+spoolman:s-(\d+)", re.IGNORECASE),
    re.compile(r"(?:^|[^\w])s-(\d+)\b", re.IGNORECASE),
    re.compile(r"^s-(\d+)$", re.IGNORECASE),
    re.compile(r"/spool/show/(\d+)"),
    re.compile(r"^(\d+)$"),
)


def parse_label(text: str) -> str | None:
    t = (text or "").strip()
    for pattern in _PATTERNS:
        m = pattern.search(t)
        if m:
            return m.group(1)
    return None
