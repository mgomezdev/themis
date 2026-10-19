"""Which printer slot satisfies a job's filament ask (spec 2026-10-19-filament-matching-design.md).

One matcher for the single ask, every model colour of a multi-material job and pinned tool slots: `evaluate(config, loaded,
conflicts)` answers "can this printer print this config's filament, on which slots, and if not why". Pure and provider-neutral;
the slot tuple is `filament_tuple.slot_tuple(slot)` and nothing here reads a provider, a material ref or a plugin id (the one
async helper, `conflicts_for`, asks the inventory *capability* for the spools bound to the slots)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import filament_tuple as ft
from .inventory import read as inventory_read, refs as inventory_refs

logger = logging.getLogger(__name__)


def is_loaded(slot: dict) -> bool:
    """False only for a tool-changer placeholder (`{"empty": True, "type": ""}` keeps list index == tool index for an empty tool).
    A hand-entered slot without a type is still a slot."""
    return bool(str(slot.get("type", "")).strip()) or not slot.get("empty")


@dataclass(frozen=True)
class SlotUse:
    """One resolved colour: `model_filament` (1-based, None for the single-filament ask) prints from loaded[`slot`]."""
    model_filament: int | None
    slot: int

    def as_dict(self) -> dict:
        return {"model_filament": self.model_filament, "slot": self.slot}


@dataclass
class Verdict:
    eligible: bool
    plan: list[SlotUse] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def config_ask(config) -> ft.FilamentTuple:
    return ft.FilamentTuple(getattr(config, "ask_manufacturer", None), getattr(config, "ask_type", None),
                            getattr(config, "ask_subtype", None), getattr(config, "ask_color", None))


def entry_ask(entry: dict) -> ft.FilamentTuple:
    return ft.FilamentTuple(entry.get("filament_manufacturer"), entry.get("filament_type"), entry.get("filament_subtype"),
                            entry.get("filament_color"))


def _label(i: int) -> str:
    return f"T{i}"


def _slot_text(slot: dict) -> str:
    t = ft.slot_tuple(slot)
    return " ".join(p for p in (t.manufacturer, t.type, t.subtype, f"#{ft.norm_color(t.color).upper()}" if t.color else None) if p) \
        or "unknown filament"


def _first_match(ask: ft.FilamentTuple, loaded: list, conflicts) -> int | None:
    for i, slot in enumerate(loaded):
        if is_loaded(slot) and i not in conflicts and ft.satisfies(ask, ft.slot_tuple(slot)):
            return i
    return None


def _why_not(ask: ft.FilamentTuple, loaded: list, conflicts, prefix: str = "") -> list[str]:
    """One line per slot saying why it does not satisfy `ask`."""
    wanted = ft.describe(ask)
    out = []
    for i, slot in enumerate(loaded):
        if not is_loaded(slot):
            out.append(f"{prefix}{_label(i)} is empty")
        elif i in conflicts:
            out.append(f"{prefix}{_label(i)}: bound spool conflicts with what the printer reports")
        else:
            out.append(f"{prefix}{_label(i)}: {_slot_text(slot)}; wanted {wanted}")
    return out


def _pinned(i: int, loaded: list, conflicts, prefix: str = "") -> str | None:
    """Why pinned slot `i` cannot be used (None = it can)."""
    if not (0 <= i < len(loaded)):
        return f"{prefix}{_label(i)} does not exist on this printer"
    if not is_loaded(loaded[i]):
        return f"{prefix}{_label(i)} is empty"
    if i in conflicts:
        return f"{prefix}{_label(i)}: bound spool conflicts with what the printer reports"
    return None


def evaluate(config, loaded: list | None, conflicts=frozenset()) -> Verdict:
    """Can the printer holding `loaded` print `config`'s filament? `conflicts` = slot indexes in conflict with their bound spool
    (see `conflicts_for`); such a slot satisfies no ask and a pinned conflicting slot makes the printer ineligible."""
    loaded = list(loaded or [])
    fmap = getattr(config, "filament_map", None) or []
    tool_index = getattr(config, "tool_index", None)

    if fmap:
        plan: list[SlotUse] = []
        reasons: list[str] = []
        for n, entry in enumerate(fmap, start=1):
            mf = entry.get("model_filament")
            prefix = f"colour {mf if mf is not None else n}: "
            pin = entry.get("tool_index")
            if pin is not None:
                problem = _pinned(int(pin), loaded, conflicts, prefix)
                if problem:
                    reasons.append(problem)
                else:
                    plan.append(SlotUse(mf, int(pin)))
                continue
            ask = entry_ask(entry)
            idx = _first_match(ask, loaded, conflicts)
            if idx is not None:
                plan.append(SlotUse(mf, idx))
            elif not loaded and not ask.is_undefined():
                reasons.append(f"{prefix}no filament configured")
            else:
                reasons.append(f"{prefix}no loaded filament matches {ft.describe(ask)}" if not loaded else "")
                reasons.extend(_why_not(ask, loaded, conflicts, prefix))
        reasons = [r for r in reasons if r]
        return Verdict(not reasons, plan if not reasons else [], reasons)

    if tool_index is not None:
        problem = _pinned(int(tool_index), loaded, conflicts)
        return Verdict(False, [], [problem]) if problem else Verdict(True, [SlotUse(None, int(tool_index))])

    ask = config_ask(config)
    idx = _first_match(ask, loaded, conflicts)
    if idx is not None:
        return Verdict(True, [SlotUse(None, idx)])
    if ask.is_undefined() and not loaded:
        return Verdict(True)                                    # a printer with no slots on record prints a "defer" ask on whatever it has
    if not loaded:
        return Verdict(False, [], ["no filament configured"])
    return Verdict(False, [], _why_not(ask, loaded, conflicts))


def slot_for_config(config, loaded: list | None, conflicts=frozenset(), plan: list | None = None) -> dict | None:
    """The slot the config prints with (its first colour's), or None. With a stored `plan` (claim time) that wins."""
    loaded = list(loaded or [])
    if plan is None:
        verdict = evaluate(config, loaded, conflicts)
        use = verdict.plan[0] if verdict.eligible and verdict.plan else None
    else:
        use = SlotUse(plan[0].get("model_filament"), plan[0]["slot"]) if plan else None
    return loaded[use.slot] if use is not None and 0 <= use.slot < len(loaded) else None


def plan_from_json(raw) -> list[SlotUse] | None:
    """A stored `slot_plan` (list of {model_filament, slot}), or None when absent / malformed."""
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("slot"), int):
            return None
        out.append(SlotUse(item.get("model_filament"), item["slot"]))
    return out


def plan_to_json(plan: list[SlotUse]) -> list[dict]:
    return [u.as_dict() for u in plan]


def resolved_filament_map(config, plan: list[SlotUse]) -> list[dict] | None:
    """The slicer's `[{model_filament, tool_index}]` for a multi-colour config (None for a single-filament one). Only the routing is
    kept: the asks that produced it must not change the slice-cache key."""
    if not getattr(config, "filament_map", None):
        return None
    return [{"model_filament": u.model_filament, "tool_index": u.slot} for u in plan]


def still_loaded(plan: list[SlotUse], loaded: list | None) -> str | None:
    """Why a plan made at claim time no longer holds (a planned slot's filament was removed), else None."""
    loaded = list(loaded or [])
    for u in plan:
        if not (0 <= u.slot < len(loaded)) or not is_loaded(loaded[u.slot]):
            return f"the filament in {_label(u.slot)} was removed"
    return None


def spool_tuple(spool) -> ft.FilamentTuple:
    m = getattr(spool, "material", None)
    if m is None:
        return ft.FilamentTuple()
    return ft.FilamentTuple(m.vendor, m.material, m.subtype, m.color_hex)


def conflicting_slots(loaded: list | None, spools_by_ref: dict) -> set[int]:
    """Indexes of slots whose bound spool's material differs from the slot's own tuple in any part known on both sides.
    Only a slot the hardware reports (`reported`, set by the AMS/tool-data merge) can conflict; a hand-entered one never does, because
    the operator set tuple and binding together."""
    out: set[int] = set()
    for i, slot in enumerate(loaded or []):
        if not slot.get("reported"):
            continue
        ref = inventory_refs.slot_spool_ref(slot)
        spool = spools_by_ref.get(ref) if ref is not None else None
        if spool is not None and ft.conflicts(ft.slot_tuple(slot), spool_tuple(spool)):
            out.add(i)
    return out


async def conflicts_for(loaded: list | None) -> set[int]:
    """`conflicting_slots` against the active inventory provider's current spools (empty without bound slots, a provider, or data:
    an unreachable inventory must never stop matching)."""
    if not any(inventory_refs.slot_spool_ref(s) is not None for s in loaded or []):
        return set()
    try:
        spools = await inventory_read.spools()
    except Exception:
        logger.warning("Inventory unreadable while checking slot conflicts")
        return set()
    return conflicting_slots(loaded, {s.ref: s for s in spools or []})
