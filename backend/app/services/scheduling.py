"""Job start windows: `jobs.not_before` and per-printer quiet hours."""
from __future__ import annotations

import re
from datetime import datetime, timezone

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_hhmm(value: str) -> int:
    """'HH:MM' → minutes since midnight. Raises ValueError on anything else."""
    m = _HHMM.match(value or "")
    if not m:
        raise ValueError(f"{value!r} is not a 24h HH:MM time")
    return int(m.group(1)) * 60 + int(m.group(2))


def parse_not_before(value: str) -> str:
    """Normalise a client timestamp to a UTC ISO string. A value with no offset is taken as UTC."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def in_quiet_hours(start: str | None, end: str | None, now_local: datetime | None = None) -> bool:
    """True when `now_local` (server-local clock) falls in [start, end). A window that wraps midnight
    (22:00–06:00) is supported; start == end, or either unset, means no quiet hours."""
    if not start or not end:
        return False
    s, e = parse_hhmm(start), parse_hhmm(end)
    if s == e:
        return False
    now = now_local or datetime.now().astimezone()
    minutes = now.hour * 60 + now.minute
    return s <= minutes < e if s < e else (minutes >= s or minutes < e)
