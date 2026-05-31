"""Precomputed compatibility index over OrcaSlicer presets.

Resolves inheritance once (the expensive part) and builds:
  - a catalog of real machine presets -> {name, printer_model, nozzle}
  - a lookup (printer_model, nozzle) -> compatible process/filament preset names

Compatibility (phase 1) is matched via each preset's resolved ``compatible_printers``
name-list, translated through the machine map to ``(printer_model, nozzle)`` — which
sidesteps the leaf-vs-system machine-name mismatch. (``compatible_printers_condition``
expressions are phase 2.) Cached; rebuilt when the user's presets change on disk.
See the ``slicer-cli-architecture`` memory.
"""
from __future__ import annotations

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
    def _build(self) -> dict:
        self._resolver.refresh()
        index = self._resolver.index

        # machine preset name -> (printer_model, nozzle); only real (instantiable)
        # presets carry both — base/common presets are skipped.
        machine_ident: dict[str, tuple[str, str]] = {}
        catalog: list[dict] = []
        for name in index["machine"]:
            try:
                cfg = self._resolver.resolve(name, "machine")
            except Exception:
                continue
            model = _first(cfg.get("printer_model"))
            nozzle = _first(cfg.get("nozzle_diameter"))
            if model and nozzle:
                machine_ident[name] = (model, nozzle)
                catalog.append({"name": name, "printer_model": model, "nozzle": nozzle})

        # (model, nozzle) -> {process, filament} via resolved compatible_printers
        compat: dict[tuple[str, str], dict[str, set]] = {}
        for category in ("process", "filament"):
            for name in index[category]:
                try:
                    cfg = self._resolver.resolve(name, category)
                except Exception:
                    continue
                cps = cfg.get("compatible_printers") or []
                if isinstance(cps, str):
                    cps = [cps] if cps else []
                for cp in cps:
                    ident = machine_ident.get(cp)
                    if ident is None:
                        continue
                    compat.setdefault(ident, {"process": set(), "filament": set()})[category].add(name)

        return {
            "machine_ident": machine_ident,
            "compat": {k: {c: sorted(v) for c, v in cats.items()} for k, cats in compat.items()},
            "catalog": sorted(catalog, key=lambda m: (m["printer_model"], m["nozzle"], m["name"])),
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
