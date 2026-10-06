"""Build plugin archives (zip / tar.gz) for installer tests."""
from __future__ import annotations

import io
import tarfile
import zipfile

TOML = """id = "{id}"
name = "{name}"
version = "{version}"
kind = "{kind}"
host_api = {host_api}
entry = "{pkg}:MANIFEST"
publisher = "Acme"
"""

CODE = '''from pydantic import BaseModel
from app.plugins import PluginManifest, UiContribution, UiTab
from app.plugins.kinds.filament_inventory import KIND


class Settings(BaseModel):
    url: str = ""


MANIFEST = PluginManifest(id="{id}", name="{name}", kind=KIND, version="{mversion}", host_api={mhost}, settings_model=Settings,
                          factory=lambda s: None, ui=UiContribution(mode="section", tabs=(UiTab("default", "Settings", "{tab}"),)){extra})
'''


def files(id="acme_inv", version="1.0.0", *, name="Acme inventory", kind="filament_inventory", host_api=1, pkg=None,
          mversion=None, mhost=None, tab="default", extra="", code=None, toml=None) -> dict[str, str]:
    pkg = pkg or id
    return {
        "themis-plugin.toml": toml if toml is not None else TOML.format(id=id, name=name, version=version, kind=kind, host_api=host_api, pkg=pkg),
        f"{pkg}/__init__.py": code if code is not None else CODE.format(id=id, name=name, mversion=mversion or version,
                                                                        mhost=mhost if mhost is not None else host_api, tab=tab, extra=extra),
        "README.md": "# plugin\n",
    }


def make_zip(members: dict[str, str | bytes], *, prefix: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(prefix + name, data)
    return buf.getvalue()


def make_zip_raw(entries: list[tuple[zipfile.ZipInfo, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for info, data in entries:
            zf.writestr(info, data)
    return buf.getvalue()


def make_tgz(members: dict[str, str | bytes], *, prefix: str = "", extra: list[tarfile.TarInfo] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            raw = data.encode() if isinstance(data, str) else data
            info = tarfile.TarInfo(prefix + name)
            info.size = len(raw)
            tf.addfile(info, io.BytesIO(raw))
        for info in extra or []:
            tf.addfile(info)
    return buf.getvalue()
