"""Which inventory spool a printer slot is bound to.

TRANSITIONAL (BIZ-217 replaces it): a slot's `spoolman_spool_id` is a *Spoolman* id, so it only means something while
Spoolman is the active provider. Applying it to any other provider would deduct from / warn about an unrelated spool,
so with another provider active the slot is treated as unbound. BIZ-217 adds provider-namespaced refs
(`slot["inventory"] = {provider, spool_ref}`) and this becomes a plain lookup."""
from __future__ import annotations

from . import provider

_LEGACY_PROVIDER = "spoolman"


def slot_spool_ref(slot: dict | None) -> str | None:
    if not slot or slot.get("spoolman_spool_id") is None:
        return None
    if provider.provider_id() != _LEGACY_PROVIDER:
        return None
    return str(slot["spoolman_spool_id"])
