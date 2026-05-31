"""Resolve OrcaSlicer inheritance-diff presets into flat, complete configs.

OrcaSlicer stores user/system presets as thin diffs that reference a parent via
`inherits`; the real settings live up the chain (often in the bundled system
presets, including nested subfolders like ``system/Elegoo/machine/ECC/``). The
CLI can't consume these directly. This service walks the chain and deep-merges
child-over-parent into a single self-contained config.

See the ``slicer-cli-architecture`` project memory for the why.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import get_orca_config_dir

CATEGORIES = ("machine", "process", "filament")

# Keys that are inheritance/bookkeeping metadata, not settings.
_META_KEYS = {"inherits", "instantiation"}


class PresetNotFoundError(Exception):
    pass


class PresetResolver:
    def __init__(self, orca_config_dir: str | None = None) -> None:
        self._root = Path(orca_config_dir) if orca_config_dir else get_orca_config_dir()
        self._index: dict[str, dict[str, Path]] | None = None

    @property
    def root(self) -> Path:
        return self._root

    # ── indexing ──────────────────────────────────────────────────────────────
    def _build_index(self) -> dict[str, dict[str, Path]]:
        """name -> path, per category. Scans ``system/`` and signed-in user account
        folders under ``user/``. Skips ``user_backup-*`` and ``plugins``. Later
        roots win, so user presets override system presets of the same name."""
        index: dict[str, dict[str, Path]] = {c: {} for c in CATEGORIES}
        roots: list[Path] = []
        system = self._root / "system"
        if system.is_dir():
            roots.append(system)
        user = self._root / "user"
        if user.is_dir():
            roots.extend(sorted(p for p in user.iterdir() if p.is_dir()))

        for root in roots:
            for jf in root.rglob("*.json"):
                parts = {p.lower() for p in jf.parts}
                category = next((c for c in CATEGORIES if c in parts), None)
                if category is None:
                    continue
                try:
                    data = json.loads(jf.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if not isinstance(data, dict):
                    continue
                name = data.get("name") or jf.stem
                index[category][name] = jf
        return index

    @property
    def index(self) -> dict[str, dict[str, Path]]:
        if self._index is None:
            self._index = self._build_index()
        return self._index

    def refresh(self) -> None:
        """Drop the cached index so the next lookup re-scans (presets are edited live)."""
        self._index = None

    # ── resolution ────────────────────────────────────────────────────────────
    def resolve(self, name: str, category: str) -> dict:
        """Walk the ``inherits`` chain and deep-merge into one flat config.

        Child values override parent values (arrays replace wholesale, per Orca
        semantics). The result carries ``type``/``name``/``from`` and drops
        inheritance metadata, so it stands alone.
        """
        if category not in CATEGORIES:
            raise ValueError(f"unknown category: {category!r}")
        merged = self._resolve_into(name, category, set())
        merged["type"] = category
        merged["name"] = name
        merged["from"] = "User"
        # printer_settings_id must be the preset NAME (the GUI writes the leaf name,
        # not the inherited vendor id) so the active-printer identity matches.
        if category == "machine":
            merged["printer_settings_id"] = name
        for k in _META_KEYS:
            merged.pop(k, None)
        return merged

    def _resolve_into(self, name: str, category: str, seen: set[str]) -> dict:
        if name in seen:
            raise PresetNotFoundError(f"inheritance cycle at {name!r}")
        seen.add(name)
        path = self.index[category].get(name)
        if path is None:
            raise PresetNotFoundError(f"preset not found: [{category}] {name!r}")
        data = json.loads(path.read_text(encoding="utf-8"))
        parent = data.get("inherits")
        merged = self._resolve_into(parent, category, seen) if parent else {}
        for key, value in data.items():
            if key == "inherits":
                continue
            merged[key] = value
        return merged
