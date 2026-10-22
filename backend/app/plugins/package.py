"""The plugin package format (spec §3.11): `themis-plugin.toml` next to the Python package. Bundled and installed plugins
use the same file; the loader checks that it and the exported `MANIFEST` agree."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .manifest import HOST_API, ID_RE, PluginError, PluginManifest, Requirement

TOML_NAME = "themis-plugin.toml"
# Also the directory name an installed version lives in, so it must never be able to climb out of it.
VERSION_RE = re.compile(r"^\d{1,6}\.\d{1,6}\.\d{1,6}(?:-[0-9A-Za-z.]{1,30})?$")
ENTRY_RE = re.compile(r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):([A-Za-z_]\w*)$")
_ALLOWED = {"id", "name", "version", "host_api", "entry", "min_themis", "publisher", "permissions", "description",
            "provides", "requires", "optional", "defines"}


@dataclass(frozen=True)
class PluginToml:
    id: str
    name: str
    version: str
    host_api: int
    module: str          # dotted module of `entry`
    attr: str            # attribute (the MANIFEST) in it
    min_themis: str | None = None
    publisher: str | None = None
    permissions: tuple[str, ...] = ()     # reserved (BIZ-200): parsed, never enforced
    description: str = ""
    provides: tuple[tuple[str, int], ...] = ()       # (capability id, contract version)
    requires: tuple[tuple[str, int], ...] = ()       # (capability id, minimum version)
    optional: tuple[tuple[str, int], ...] = ()
    defines: tuple[str, ...] = ()                    # capability ids this plugin introduces

    @property
    def entry(self) -> str:
        return f"{self.module}:{self.attr}"

    @property
    def top_package(self) -> str:
        return self.module.split(".")[0]


def _str(data: dict, key: str, required: bool = True, limit: int = 200) -> str | None:
    v = data.get(key)
    if v is None:
        if required:
            raise PluginError(f"{TOML_NAME}: `{key}` is required")
        return None
    if not isinstance(v, str) or not v.strip() or len(v) > limit or any(ord(c) < 32 for c in v):
        raise PluginError(f"{TOML_NAME}: `{key}` must be a short single-line string")
    return v


def _caps(data: dict, key: str) -> tuple[tuple[str, int], ...]:
    raw = data.get(key, [])
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw) or len(raw) > 50:
        raise PluginError(f"{TOML_NAME}: `{key}` must be a list of capability ids")
    out = tuple((r.capability, r.min_version) for r in map(Requirement.parse, raw))
    if len({c for c, _ in out}) != len(out):
        raise PluginError(f"{TOML_NAME}: `{key}` lists a capability twice")
    return out


def parse_toml(text: str, *, reserved_ids: frozenset[str] = frozenset()) -> PluginToml:
    """Validate the toml: id format (and not a reserved bundled id), semver version, host_api, capability lists, entry shape."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise PluginError(f"{TOML_NAME} is not valid TOML: {e}") from e
    unknown = sorted(set(data) - _ALLOWED)
    if unknown:
        raise PluginError(f"{TOML_NAME}: unknown keys {unknown}")
    pid = _str(data, "id") or ""
    if not ID_RE.match(pid):
        raise PluginError(f"{TOML_NAME}: id {pid!r} must match {ID_RE.pattern}")
    if pid in reserved_ids:
        raise PluginError(f"{TOML_NAME}: id {pid!r} is reserved by a bundled plugin")
    version = _str(data, "version") or ""
    if not VERSION_RE.match(version):
        raise PluginError(f"{TOML_NAME}: version {version!r} must be semver (MAJOR.MINOR.PATCH[-pre])")
    host_api = data.get("host_api")
    if isinstance(host_api, bool) or not isinstance(host_api, int):
        raise PluginError(f"{TOML_NAME}: `host_api` must be an integer")
    if host_api != HOST_API:
        raise PluginError(f"{TOML_NAME}: targets host_api {host_api}; this Themis provides {HOST_API}")
    m = ENTRY_RE.match(_str(data, "entry") or "")
    if not m:
        raise PluginError(f"{TOML_NAME}: `entry` must look like 'package.module:MANIFEST'")
    perms = data.get("permissions", [])
    if not isinstance(perms, list) or not all(isinstance(p, str) for p in perms):
        raise PluginError(f"{TOML_NAME}: `permissions` must be a list of strings")
    if any("@" in x for x in data.get("defines", []) if isinstance(x, str)):
        raise PluginError(f"{TOML_NAME}: `defines` takes plain capability ids, no @version")
    return PluginToml(id=pid, name=_str(data, "name") or pid, version=version, host_api=host_api,
                      module=m.group(1), attr=m.group(2), min_themis=_str(data, "min_themis", False, 40),
                      publisher=_str(data, "publisher", False), permissions=tuple(perms),
                      description=_str(data, "description", False, 1000) or "",
                      provides=_caps(data, "provides"), requires=_caps(data, "requires"), optional=_caps(data, "optional"),
                      defines=tuple(c for c, _ in _caps(data, "defines")))


def read_toml(directory: Path, *, reserved_ids: frozenset[str] = frozenset()) -> PluginToml:
    path = directory / TOML_NAME
    if not path.is_file():
        raise PluginError(f"{TOML_NAME} not found at the package root")
    if path.stat().st_size > 16_384:
        raise PluginError(f"{TOML_NAME} is too large")
    return parse_toml(path.read_text(encoding="utf-8"), reserved_ids=reserved_ids)


def entry_file_exists(root: Path, t: PluginToml) -> bool:
    base = root.joinpath(*t.module.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def check_matches(t: PluginToml, manifest: object) -> None:
    """The exported MANIFEST must be the one the toml describes (id, version, host_api, and its capability lists)."""
    if not isinstance(manifest, PluginManifest):
        raise PluginError(f"entry {t.entry} is not a PluginManifest")
    for field in ("id", "version", "host_api"):
        if getattr(manifest, field) != getattr(t, field):
            raise PluginError(f"{TOML_NAME} {field} {getattr(t, field)!r} != MANIFEST {field} {getattr(manifest, field)!r}")
    got = {
        "provides": sorted((c, p.version) for c, p in manifest.provides.items()),
        "requires": sorted((r.capability, r.min_version) for r in manifest.requires),
        "optional": sorted((r.capability, r.min_version) for r in manifest.optional),
        "defines": sorted(d.id for d in manifest.defines),
    }
    for field, have in got.items():
        if sorted(getattr(t, field)) != have:
            raise PluginError(f"{TOML_NAME} {field} {sorted(getattr(t, field))!r} != MANIFEST {field} {have!r}")


def _parts(v: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(x) for x in v.split("-")[0].split("."))
    except ValueError:
        return None


def check_min_themis(t: PluginToml, themis_version: str) -> None:
    """`min_themis` is compared numerically against the running version when both look like dotted numbers."""
    if t.min_themis is None:
        return
    need, have = _parts(t.min_themis), _parts(themis_version.lstrip("v"))
    if need is None:
        raise PluginError(f"{TOML_NAME}: min_themis {t.min_themis!r} is not a dotted version")
    if have is not None and have < need:
        raise PluginError(f"requires Themis {t.min_themis} or newer (running {themis_version})")
