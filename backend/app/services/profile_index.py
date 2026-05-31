"""Precomputed compatibility index over OrcaSlicer presets.

Resolves inheritance once (the expensive part) and builds:
  - a catalog of real machine presets -> {name, printer_model, nozzle}
  - a lookup (printer_model, nozzle) -> compatible process/filament preset names

Compatibility is matched two ways, mirroring OrcaSlicer's own preset-dropdown logic:
  1. **Name-list** — a preset's resolved ``compatible_printers`` is translated through
     the machine map to ``(printer_model, nozzle)``, sidestepping the leaf-vs-system
     machine-name mismatch.
  2. **Universal** — a *selectable* preset (raw ``instantiation`` != "false") with an
     empty ``compatible_printers`` AND empty ``compatible_printers_condition`` is
     compatible with every printer (this is how the OrcaFilamentLibrary ``Generic *``
     filaments are shipped). It is added to every machine.

(A ``compatible_printers_condition`` *expression* evaluator was scoped as "phase 2" but
the installed config has zero non-empty conditions — every conditioned preset also
carries a name-list — so it is unbuilt. A non-empty condition without a name-list would
fall through here; revisit only if such presets appear.) Cached; rebuilt when the user's
presets change on disk. See the ``slicer-cli-architecture`` memory.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .preset_resolver import PresetResolver

logger = logging.getLogger(__name__)


def _first(value) -> str:
    if isinstance(value, list):
        return str(value[0]).strip() if value else ""
    return str(value or "").strip()


class ProfileIndex:
    def __init__(self, resolver: PresetResolver | None = None) -> None:
        self._resolver = resolver or PresetResolver()
        self._data: dict | None = None
        self._signature: tuple | None = None

    # ── caching ────────────────────────────────────────────────────────────────
    def _user_signature(self) -> tuple:
        """Cheap change-detection over the user's own presets (the ones that get
        edited live). System presets change only on an Orca update."""
        user = self._resolver.root / "user"
        if not user.is_dir():
            return ()
        sig: list[tuple] = []
        for p in user.rglob("*.json"):
            try:
                sig.append((p.name, p.stat().st_mtime_ns))
            except OSError:
                pass
        return tuple(sorted(sig))

    def _ensure(self) -> dict:
        sig = self._user_signature()
        if self._data is None or sig != self._signature:
            self._data = self._build()
            self._signature = sig
        return self._data

    def refresh(self) -> None:
        self._data = None
        self._signature = None

    # ── build ────────────────────────────────────────────────────────────────
    def _vendor_path(self, path: Path) -> str | None:
        """Vendor = the folder under system/, e.g. system/Elegoo/... -> 'Elegoo'."""
        parts = path.parts
        if "system" in parts:
            i = parts.index("system")
            if i + 1 < len(parts):
                return parts[i + 1]
        return None

    def _vendor_of(self, name: str, machine_index: dict[str, Path], _seen=None) -> str:
        """System presets get their vendor from the folder; user presets inherit it
        from their system ancestor; otherwise 'Custom'."""
        _seen = _seen or set()
        path = machine_index.get(name)
        if path is None or name in _seen:
            return "Custom"
        _seen.add(name)
        v = self._vendor_path(path)
        if v:
            return v
        try:
            parent = json.loads(path.read_text(encoding="utf-8")).get("inherits")
        except (OSError, json.JSONDecodeError):
            parent = None
        return self._vendor_of(parent, machine_index, _seen) if parent else "Custom"

    def _is_selectable(self, path: Path) -> bool:
        """A preset is user-selectable unless its raw leaf marks ``instantiation`` false
        (the abstract ``@base`` presets do). ``resolve()`` strips this key, so read raw.
        User presets omit the field entirely — those are selectable."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return str(raw.get("instantiation", "")).lower() != "false"

    def _build(self) -> dict:
        self._resolver.refresh()
        index = self._resolver.index
        machine_paths = index["machine"]

        # machine preset name -> (printer_model, nozzle); only real (instantiable)
        # presets carry both — base/common presets are skipped.
        machine_ident: dict[str, tuple[str, str]] = {}
        catalog: list[dict] = []
        for name, path in machine_paths.items():
            try:
                cfg = self._resolver.resolve(name, "machine")
            except Exception:
                continue
            model = _first(cfg.get("printer_model"))
            nozzle = _first(cfg.get("nozzle_diameter"))
            if model and nozzle:
                machine_ident[name] = (model, nozzle)
                catalog.append({
                    "name": name,
                    "vendor": self._vendor_of(name, machine_paths),
                    "printer_model": model,
                    "nozzle": nozzle,
                    "source": "system" if "system" in path.parts else "user",
                })

        # (model, nozzle) -> {process, filament}. A preset reaches a machine either by
        # naming it in compatible_printers, or by being universal (selectable + no
        # compat declaration at all — e.g. the Generic @System filaments).
        all_idents = set(machine_ident.values())
        compat: dict[tuple[str, str], dict[str, set]] = {
            ident: {"process": set(), "filament": set()} for ident in all_idents
        }
        for category in ("process", "filament"):
            for name, path in index[category].items():
                try:
                    cfg = self._resolver.resolve(name, category)
                except Exception:
                    continue
                cps = cfg.get("compatible_printers") or []
                if isinstance(cps, str):
                    cps = [cps] if cps else []
                cond = str(cfg.get("compatible_printers_condition") or "").strip()
                if not cps and not cond:
                    # Universal — but skip abstract @base presets (not user-selectable).
                    if self._is_selectable(path):
                        for ident in all_idents:
                            compat[ident][category].add(name)
                    continue
                for cp in cps:
                    ident = machine_ident.get(cp)
                    if ident is None:
                        continue
                    compat[ident][category].add(name)

        return {
            "machine_ident": machine_ident,
            "compat": {k: {c: sorted(v) for c, v in cats.items()} for k, cats in compat.items()},
            "catalog": sorted(catalog, key=lambda m: (m["vendor"], m["printer_model"], m["nozzle"], m["name"])),
        }

    # ── public API ───────────────────────────────────────────────────────────
    def machine_catalog(self) -> list[dict]:
        """Real selectable machine presets: [{name, printer_model, nozzle}]."""
        return self._ensure()["catalog"]

    def compatible_profiles(self, machine_preset: str) -> dict:
        """{print_profiles, filament_profiles} compatible with the given machine
        preset, resolved via its (printer_model, nozzle)."""
        data = self._ensure()
        ident = data["machine_ident"].get(machine_preset)
        if ident is None:
            return {"print_profiles": [], "filament_profiles": []}
        cell = data["compat"].get(ident, {"process": [], "filament": []})
        return {"print_profiles": cell.get("process", []), "filament_profiles": cell.get("filament", [])}
