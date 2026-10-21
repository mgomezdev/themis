"""Provider-namespaced inventory refs and the dual-write normalizer (BIZ-202 §4).

A slot binds to a spool as `slot["inventory"] = {"provider": "<plugin id>", "spool_ref": "<ref>"}` (never `filament_id`: for
Bambu that already means an AMS tray code). A job's ask no longer references a material (it is a filament tuple, see
services/filament_tuple.py); the ref is only for weight deduction. For one release the legacy Spoolman keys (`slot["spoolman_spool_id"]`, `filament_id`)
are still written next to them while the provider is Spoolman, because old frontends, external API clients and AMS reports
keep writing the old keys — so EVERY writer runs through this module:

* `normalize_slots` — `PATCH /printers`, printer create, fleet import (whole-list replace, resolved against the stored slots).
* `preserve_slot_keys` — the AMS report merge keeps every Themis-owned key (not just two named ones).

The legacy key names live here (and in migrations/models) only; BIZ-221 removes the fallbacks."""
from __future__ import annotations

from ..filament_tuple import clean, split_type
from . import provider

LEGACY_PROVIDER = "spoolman"
LEGACY_SLOT_KEY = "spoolman_spool_id"
# Slot keys Themis owns: an AMS/vendor report must never wipe them (the vendor client only reports hardware facts).
THEMIS_SLOT_KEYS = ("filament_profile", LEGACY_SLOT_KEY, "inventory")


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


# --- slot <-> spool binding ----------------------------------------------------------------------------------------------

def slot_inventory(slot: dict | None) -> dict | None:
    """The slot's `inventory` binding if it is well formed, else None."""
    inv = (slot or {}).get("inventory")
    if isinstance(inv, dict) and not _blank(inv.get("provider")) and not _blank(inv.get("spool_ref")):
        return {"provider": str(inv["provider"]), "spool_ref": str(inv["spool_ref"])}
    return None


def slot_spool_ref(slot: dict | None) -> str | None:
    """The spool ref this slot is bound to *for the active provider*, else None (a binding to another provider is never
    applied to the active one). Slots written before the migration/dual-write only carry the legacy Spoolman key."""
    pid = provider.provider_id()
    if not slot or pid is None:
        return None
    inv = slot_inventory(slot)
    if inv is not None:
        return inv["spool_ref"] if inv["provider"] == pid else None
    legacy = slot.get(LEGACY_SLOT_KEY)
    return str(legacy) if not _blank(legacy) and pid == LEGACY_PROVIDER else None


def _slot_tuple_keys(out: dict) -> dict:
    """A slot with a type always has a subtype (a legacy "PLA+" is split with the shared suffix rules; plain = "standard") and a
    `manufacturer` key (None = unknown). A typeless slot (an empty tool placeholder, a bare hand entry) gets no invented subtype."""
    if not clean(out.get("type")):
        return out
    out["type"] = clean(out["type"])
    subtype = clean(out.get("subtype"))
    if not subtype:
        out["type"], subtype = split_type(out["type"])
    out["subtype"] = subtype
    out["manufacturer"] = clean(out.get("manufacturer"))
    return out


def normalize_slot(slot: dict, previous: dict | None = None) -> dict:
    """One slot as it will be stored: `inventory` and the legacy key agree. Conflicts resolve by *what changed* relative to
    the stored slot: an edited `inventory` wins (new client); otherwise an edited legacy key wins (old client, which also
    echoes the stale `inventory` it read); otherwise nothing changed and the stored binding is kept."""
    out = _slot_tuple_keys(dict(slot))
    out.pop("reported", None)                       # hardware-owned: a client can neither claim nor clear it (only the telemetry merge sets it)
    if previous and previous.get("reported"):
        out["reported"] = True                      # a client edit must not turn a reported slot into a hand-entered one
    prev_inv = slot_inventory(previous)
    prev_legacy = (previous or {}).get(LEGACY_SLOT_KEY)
    legacy = slot.get(LEGACY_SLOT_KEY)
    legacy_set = not _blank(legacy)
    has_inventory_key = "inventory" in slot
    inv = slot_inventory(slot)

    inventory_changed = has_inventory_key and inv != prev_inv
    legacy_changed = str(legacy if legacy_set else "") != str(prev_legacy if not _blank(prev_legacy) else "")
    if inventory_changed:
        chosen = inv
    elif legacy_changed:
        chosen = {"provider": LEGACY_PROVIDER, "spool_ref": str(legacy)} if legacy_set else None
    elif has_inventory_key:
        chosen = inv
    elif prev_inv is not None:
        chosen = prev_inv                                            # an old client cannot see (or have edited) it: keep it
    else:
        chosen = {"provider": LEGACY_PROVIDER, "spool_ref": str(legacy)} if legacy_set else None

    if chosen is None:
        out.pop("inventory", None)
    else:
        out["inventory"] = chosen
    # mirror the legacy key only for the legacy provider
    if chosen is not None and chosen["provider"] == LEGACY_PROVIDER:
        ref = chosen["spool_ref"]
        # keep the type old clients compare against (an int id), unless the caller's own value already matches
        out[LEGACY_SLOT_KEY] = legacy if legacy_set and str(legacy) == ref else (int(ref) if ref.isdigit() else ref)
    elif LEGACY_SLOT_KEY in slot or LEGACY_SLOT_KEY in out:
        out[LEGACY_SLOT_KEY] = None
    return out


def normalize_slots(new: list[dict] | None, previous: list[dict] | None = None) -> list[dict]:
    """`new` as it will be stored, each slot resolved against the stored slot with the same `slot` number (else index)."""
    prev = list(previous or [])
    by_number = {s.get("slot"): s for s in prev if isinstance(s, dict) and s.get("slot") is not None}
    out = []
    for i, slot in enumerate(new or []):
        if not isinstance(slot, dict):
            out.append(slot)
            continue
        before = by_number.get(slot.get("slot")) if slot.get("slot") is not None else (prev[i] if i < len(prev) else None)
        out.append(normalize_slot(slot, before))
    return out


def preserve_slot_keys(previous: dict | None, fresh: dict) -> dict:
    """`fresh` (a vendor report for one slot) plus every Themis-owned key of the stored slot."""
    out = dict(fresh)
    previous = previous or {}
    for key in THEMIS_SLOT_KEYS:
        if key in previous or key != "inventory":          # the two legacy keys are always present (None when unset), as before
            out[key] = previous.get(key)
    return out
