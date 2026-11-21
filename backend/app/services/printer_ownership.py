"""One physical network printer belongs to one plugin, including dormant bindings.

Host names are compared without case, whitespace or a trailing DNS dot. Ports and
credentials do not create another device. Serial numbers detect a known device
at a changed address; DNS aliases without a shared serial cannot be inferred.
"""
from sqlalchemy import select

from ..models import Printer

ERROR = "printer ownership conflict"


def identifiers(cfg: dict | None) -> tuple[str, str]:
    cfg = cfg or {}
    host = str(cfg.get("ip_address") or cfg.get("host") or "").strip().lower().rstrip(".")
    serial = str(cfg.get("serial_number") or "").strip().lower()
    return host, serial


def overlaps(a: dict | None, b: dict | None) -> bool:
    ah, az = identifiers(a)
    bh, bz = identifiers(b)
    return bool((ah and ah == bh) or (az and az == bz))


async def conflict(session, cfg: dict, exclude_id: int | None = None) -> Printer | None:
    for other in (await session.execute(select(Printer).order_by(Printer.id))).scalars():
        if other.id != exclude_id and overlaps(cfg, other.connection_config):
            return other
    return None


def reason(other: Printer) -> str:
    return f"{other.name} already owns this printer; edit or delete that binding before adding another plugin"
