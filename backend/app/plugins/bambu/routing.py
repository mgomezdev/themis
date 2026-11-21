"""Bambu wire-format tray mapping. Core supplies only a neutral slot selection."""
from ...services.filament_matching import MAX_MODEL_FILAMENTS

def ams_mapping(plan: list, loaded: list) -> list[int] | None:
    """Printer tray ids indexed by model filament (entry N-1 = filament N), never list indices or spool refs. A filament number the
    plan doesn't use (a plate printing filaments 1 and 3 of a project) is -1, the documented "unused" value of `ams_mapping`."""
    if not plan:
        return None
    if any(not 0 <= u.slot < len(loaded) for u in plan):
        raise ValueError("A mapped filament slot was removed before printing")
    by_filament: dict[int, int | None] = {}
    for u in plan:
        mf = 1 if u.model_filament is None else u.model_filament       # the single-filament ask is filament 1
        if type(mf) is not int or not 1 <= mf <= MAX_MODEL_FILAMENTS or mf in by_filament:
            raise ValueError(f"Filament {mf} is mapped more than once or is not a filament number")
        slot = loaded[u.slot]
        position_id = slot.get('position_id')
        by_filament[mf] = int(position_id) if position_id is not None else slot.get("ams_tray_id")
    if all(t is None for t in by_filament.values()):
        return None  # this printer does not use AMS tray identifiers
    if any(type(t) is not int or t < 0 for t in by_filament.values()):
        raise ValueError("Every mapped filament needs a valid AMS tray identifier")
    return [by_filament.get(n, -1) for n in range(1, max(by_filament) + 1)]
