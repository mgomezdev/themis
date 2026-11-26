"""A printer model's picture for the Fleet card. The plugin owns the PNG (`PrinterModel.image`, a path inside its package);
core resolves it through the plugin registry and serves the bytes. Nothing here names a vendor or stores an image."""
from __future__ import annotations

from pathlib import Path

from ..plugins import get_plugin, plugin_root
from ..plugins.host import plugin_host


def printer_image_path(plugin_id: str | None, model_id: str | None) -> Path | None:
    """The PNG the plugin declares for this model, or None: no plugin / disabled / no declaration / file missing / path escapes the
    plugin's package."""
    if not plugin_id or not model_id:
        return None
    manifest = get_plugin(plugin_id)
    root = plugin_root(plugin_id)
    if manifest is None or root is None or not plugin_host.is_enabled(plugin_id):
        return None
    for mfr in manifest.manufacturers:
        for model in mfr.models:
            if model.id == model_id and model.image:
                path = (root / model.image).resolve()
                if path.suffix.lower() != ".png" or not path.is_relative_to(root.resolve()) or not path.is_file():
                    return None
                return path
    return None
