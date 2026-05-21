from __future__ import annotations
import json
from pathlib import Path

from ..config import get_orca_config_dir


class ProfileService:
    def __init__(self, orca_config_dir: str | None = None) -> None:
        self._root = Path(orca_config_dir) if orca_config_dir else get_orca_config_dir()

    def _scan_presets(self, *relative_dirs: str) -> list[dict]:
        presets = []
        for rel in relative_dirs:
            rel_path = self._root / rel
            if not rel_path.exists():
                continue
            for json_file in rel_path.glob("**/*.json"):
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and "name" in data:
                        presets.append(data)
                except (json.JSONDecodeError, OSError):
                    pass
        return presets

    def get_printer_preset_names(self) -> list[str]:
        presets = self._scan_presets("system", "user/default/machine")
        seen: set[str] = set()
        result = []
        for p in presets:
            name = p["name"]
            if name not in seen:
                seen.add(name)
                result.append(name)
        return result

    def get_compatible_profiles(self, current_printer_profile: str) -> dict:
        print_profiles = []
        filament_profiles = []

        for p in self._scan_presets("user/default/process", "system"):
            if current_printer_profile in p.get("compatible_printers", []):
                print_profiles.append(p["name"])

        for p in self._scan_presets("user/default/filament", "system"):
            if current_printer_profile in p.get("compatible_printers", []):
                filament_profiles.append(p["name"])

        return {"print_profiles": print_profiles, "filament_profiles": filament_profiles}
