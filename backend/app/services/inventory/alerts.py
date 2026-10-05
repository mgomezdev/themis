"""Low-inventory alerts: after each sync, any spool whose remaining grams fell below its threshold raises a `spool.low`
event (webhook + notification channels) once, until it is refilled/replaced.

Threshold = the material's override if set, else the default; with neither, nothing alerts. Needs TRACKS_WEIGHT.
State (`inventory_config`) is namespaced by provider, so alert state survives a provider switch."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ...models import NotificationConfig, WebhookConfig
from ...plugins.kinds.filament_inventory import InvSpool
from .. import notification_service, webhook_service
from . import config as inv_config

logger = logging.getLogger("app")

EVENT = "spool.low"


@dataclass(frozen=True)
class LowSpool:
    spool_ref: str
    material_ref: str | None
    name: str
    remaining_g: float
    threshold_g: float
    location: str | None
    provider: str = ""

    @property
    def spool_id(self) -> int | str:
        """Payload keeps the integer id when the ref is one (the key external consumers always got)."""
        return int(self.spool_ref) if self.spool_ref.isdigit() else self.spool_ref

    @property
    def filament_id(self) -> int | str | None:
        r = self.material_ref
        return int(r) if r is not None and r.isdigit() else r


def threshold_for(material_ref: str | None, default_g: float | None, overrides: dict | None) -> float | None:
    if material_ref is not None and overrides:
        override = overrides.get(str(material_ref))
        if override is not None:
            return float(override)
    return float(default_g) if default_g is not None else None


def spool_name(spool: InvSpool) -> str:
    m = spool.material
    name = (m.name if m else "") or f"spool {spool.ref}"
    return f"{m.vendor} {name}" if m and m.vendor else name


def find_low(spools: list[InvSpool], default_g: float | None, overrides: dict | None, provider: str = "") -> list[LowSpool]:
    out = []
    for s in spools:
        remaining = s.remaining_g
        if remaining is None or s.archived:
            continue
        limit = threshold_for(s.material_ref, default_g, overrides)
        if limit is None or remaining >= limit:
            continue
        out.append(LowSpool(s.ref, s.material_ref, spool_name(s), float(remaining), limit,
                            (s.location or "").strip() or None, provider))
    return out


def message_for(low: LowSpool) -> tuple[str, str]:
    where = f" ({low.location})" if low.location else ""
    return ("Themis: spool running low",
            f"{low.name}{where} has {low.remaining_g:.0f} g left (alert below {low.threshold_g:.0f} g).")


async def process(session: AsyncSession, provider_id: str, spools: list[InvSpool]) -> list[LowSpool]:
    """Alert for spools newly below threshold and remember the ones actually delivered (so each drop alerts once). A
    spool whose delivery failed is *not* remembered and is retried at the next sync. A failure to load the notification
    configs (a DB error) propagates — the caller runs this inside a savepoint."""
    cfg = await inv_config.get_config(session)
    low = find_low(spools, cfg.low_stock_default_g, inv_config.overrides_for(cfg, provider_id), provider_id)
    mine = lambda key: inv_config.split_ns(key)[0] == provider_id          # noqa: E731
    stored = list(cfg.low_stock_alerted or [])
    already = {inv_config.split_ns(k)[1] for k in stored if mine(k)}
    still_low = {l.spool_ref for l in low}
    fresh = [l for l in low if l.spool_ref not in already]
    webhook = await session.get(WebhookConfig, 1) if fresh else None
    notif = await session.get(NotificationConfig, 1) if fresh else None

    delivered: list[LowSpool] = []
    for l in fresh:
        try:
            _deliver(webhook, notif, l)
            delivered.append(l)
        except Exception:
            logger.exception("Low-stock alert for spool %s could not be sent; will retry at the next sync", l.spool_ref)

    remembered = (already & still_low) | {l.spool_ref for l in delivered}     # refilled spools drop out (re-armed)
    new_state = sorted([k for k in stored if not mine(k)] + [inv_config.ns(provider_id, r) for r in remembered])
    if new_state != sorted(stored):
        cfg.low_stock_alerted = new_state
    return delivered


def _deliver(webhook: WebhookConfig | None, notif: NotificationConfig | None, low: LowSpool) -> None:
    if webhook and webhook.url and (not webhook.events or EVENT in webhook.events):
        webhook_service.schedule(webhook.url, webhook.secret, EVENT, None, {
            "spool_id": low.spool_id, "filament_id": low.filament_id, "name": low.name,
            "remaining_g": low.remaining_g, "threshold_g": low.threshold_g, "location": low.location})
    if notif and (notif.ntfy_enabled or notif.discord_enabled or notif.email_enabled):
        title, message = message_for(low)
        asyncio.create_task(notification_service.dispatch(notif, EVENT, None, title, message))
