"""Slicing cache (BIZ-189/191): cache keys for sliced versions, staleness, and decision logging.

A cached version is a library .gcode / .gcode.3mf that a model sliced to (`SlicedVersion`). Its `cache_key` hashes
every input that changes the slicer's output, so a lookup can tell a PETG version from a PLA one, a 0.12 mm from a
0.20 mm, an X1C from a Centauri. What it deliberately leaves out:

- filament colour — only the preview/metadata, never the toolpaths;
- the *contents* of the named presets and the OrcaSlicer version. Those are recorded separately and decide whether a
  version is **stale** (`staleness`), which the `slice_cache_use_latest_settings` setting then acts on. A stale version
  is still the same cache entry — never a miss by key.

Every decision is logged through `log_event` as one greppable `slice_cache event=<name> key=value …` line, and the
latest one is stored on the job (`jobs.slice_cache_info`, built by `decision_info`) so it survives log rotation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .slicer_service import SliceRequest, _export_3mf_name

logger = logging.getLogger("app.services.slice_cache")

# Events and their log levels (the field lists are documented in docs/agent/data-model.md § sliced_versions).
_LEVELS = {
    "lookup": logging.DEBUG,
    "hit_slice_skipped": logging.INFO,
    "miss": logging.INFO,
    "saved": logging.INFO,
    "save_duplicate_skipped": logging.INFO,
    "save_failed": logging.WARNING,
    "pack_reused": logging.INFO,
    "pack_new": logging.INFO,
    "version_detached": logging.INFO,
}
MISS_REASONS = ("no_version", "stale_resliced", "file_missing", "uncacheable", "lookup_error", "cache_disabled")


def canonical_json(value: Any) -> str:
    """Stable JSON: sorted keys, no whitespace — the same value always hashes the same."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class CacheKeyInputs:
    """Everything that changes a slice's output. Build it with `key_inputs` from the exact `SliceRequest` the queue
    engine slices with — never re-derive the fields separately, or save and lookup drift apart."""
    source_content_hash: str
    plate_number: int
    machine_preset: str
    process_preset: str
    filament_presets: tuple[str, ...]
    extra_config: dict
    tool_index: int | None
    filament_map: list | None
    artifact_kind: str   # gcode | gcode_3mf

    def as_dict(self) -> dict:
        d = asdict(self)
        d["filament_presets"] = list(self.filament_presets)
        return d


def key_inputs(
    req: SliceRequest, source_content_hash: str | None, tool_index: int | None, filament_map: list | None,
) -> CacheKeyInputs | None:
    """`tool_index`/`filament_map` aren't on the request (they reach the slicer as a 3MF remap via `prepare_hook`) but
    they change the output, so they're part of the key. `filament_map` must be the RESOLVED map (slot indices, after
    `queue_engine._resolve_filament_map`) — exactly what the remap used — or save and lookup drift apart.

    Returns None (uncacheable) when the source has no content hash: without it two different models with the same
    settings would share a key and one would print the other's gcode."""
    if not source_content_hash:
        return None
    return CacheKeyInputs(
        source_content_hash=source_content_hash,
        plate_number=int(req.plate_number),
        machine_preset=req.machine_preset or "",
        process_preset=req.process_preset or "",
        filament_presets=tuple(req.filament_presets or ()),
        extra_config=dict(req.extra_config or {}),
        tool_index=tool_index,
        filament_map=list(filament_map) if filament_map else None,
        artifact_kind="gcode_3mf" if _export_3mf_name(req.export_args) is not None else "gcode",
    )


def cache_key(inputs: CacheKeyInputs) -> str:
    return sha256_of(inputs.as_dict())


def key_fields(inputs: CacheKeyInputs) -> dict:
    """The key inputs as log/info fields (bulky ones hashed), so a miss can be explained by diffing two lines."""
    return {
        "source_content_hash": inputs.source_content_hash,
        "plate": inputs.plate_number,
        "machine_preset": inputs.machine_preset,
        "process_preset": inputs.process_preset,
        "filament_presets": list(inputs.filament_presets),
        "extra_config_hash": sha256_of(inputs.extra_config),
        "filament_map_hash": sha256_of(inputs.filament_map) if inputs.filament_map else None,
        "tool_index": inputs.tool_index,
        "artifact_kind": inputs.artifact_kind,
    }


# ---- staleness ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SlicerFingerprint:
    """What the slicer would currently produce for a set of presets, beyond their names. Either part is None when it
    couldn't be determined (sidecar unreachable, preset no longer in the catalog, version not reported)."""
    preset_content_hash: str | None
    slicer_version: str | None


def current_fingerprint(
    machine_preset: str, process_preset: str, filament_presets: list[str], sidecar_url: str | None,
) -> SlicerFingerprint:
    """Blocking (HTTP to the Laminus sidecar) — call via asyncio.to_thread. Never raises: an unreachable sidecar or an
    unresolvable preset yields None parts, which `staleness` treats as unknown."""
    if not sidecar_url:
        return SlicerFingerprint(None, None)
    from .laminus_sidecar_client import LaminusSidecarClient
    from .slicer_service import resolve_preset_uuids
    client = LaminusSidecarClient(sidecar_url, timeout=10)
    preset_hash: str | None = None
    version: str | None = None
    try:
        machine_uuid, process_uuid, filament_uuids = resolve_preset_uuids(
            machine_preset, process_preset, list(filament_presets), sidecar_url)
        preset_hash = sha256_of(client.get_merged_config(machine_uuid, process_uuid, filament_uuids))
    except Exception as exc:   # SliceError (unresolvable preset), SidecarError, transport errors
        logger.debug("slice_cache could not fingerprint presets: %s", exc)
    try:
        health = client.health()
        version = health.get("orca_version") or health.get("orcaslicer_version") or None
    except Exception as exc:
        logger.debug("slice_cache could not read the slicer version: %s", exc)
    return SlicerFingerprint(preset_hash, normalize_version(version))


def normalize_version(raw: Any) -> str | None:
    """`orca_version` ("2.3.1") and `orcaslicer_version` ("OrcaSlicer 2.3.1-beta") spell the same release
    differently; compare only the dotted number so a fallback between them is never a false "slicer changed"."""
    if not raw:
        return None
    m = re.search(r"\d+(?:\.\d+)+", str(raw))
    return m.group(0) if m else str(raw).strip() or None


_FINGERPRINT_TTL = 60.0
_UNKNOWN_TTL = 10.0
_fingerprints: dict[tuple, tuple[float, SlicerFingerprint]] = {}


def cached_fingerprint(
    machine_preset: str, process_preset: str, filament_presets: list[str], sidecar_url: str | None,
) -> SlicerFingerprint:
    """`current_fingerprint`, memoised for a minute per preset set — listing a model's versions or a burst of claims
    shouldn't each pay the sidecar round-trips. Blocking; call via asyncio.to_thread."""
    import time
    key = (machine_preset, process_preset, tuple(filament_presets), sidecar_url)
    hit = _fingerprints.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < (_FINGERPRINT_TTL if (hit[1].preset_content_hash or hit[1].slicer_version)
                               else _UNKNOWN_TTL):
        return hit[1]
    fp = current_fingerprint(machine_preset, process_preset, filament_presets, sidecar_url)
    _fingerprints[key] = (now, fp)   # "unknown" too, briefly: a hung sidecar shouldn't stall every request
    return fp


def staleness(
    stored_preset_hash: str | None, stored_version: str | None, current: SlicerFingerprint,
) -> tuple[bool | None, list[str]]:
    """(stale, reasons). An axis only counts when both sides are known. Stale if either known axis differs; not stale
    if at least one axis was compared and none differ; None (unknown) if nothing could be compared."""
    reasons: list[str] = []
    compared = False
    if stored_preset_hash and current.preset_content_hash:
        compared = True
        if stored_preset_hash != current.preset_content_hash:
            reasons.append("presets_changed")
    if stored_version and current.slicer_version:
        compared = True
        if stored_version != current.slicer_version:
            reasons.append("slicer_version_changed")
    if reasons:
        return True, reasons
    return (False if compared else None), []


# ---- logging + the per-job record --------------------------------------------------------------------------------

def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, dict)):
        return canonical_json(value)
    text = str(value)
    return json.dumps(text) if (not text or re.search(r"""[\s="']""", text)) else text


def log_event(event: str, /, *, _level: int | None = None, **fields: Any) -> None:
    """One line: `slice_cache event=<event> k=v …` (fields in the order given; values quoted when they contain spaces,
    lists/dicts as compact JSON, None as `-`). Hashes are logged in full."""
    level = _level
    if level is None:
        level = _LEVELS.get(event, logging.INFO)
        if event == "miss" and fields.get("reason") == "cache_disabled":
            level = logging.DEBUG
        if event == "hit_slice_skipped" and fields.get("stale") is True:
            level = logging.WARNING   # a pinned stale version printed: make it stand out
    parts = " ".join(f"{k}={_fmt(v)}" for k, v in fields.items())
    logger.log(level, "slice_cache event=%s%s", event, f" {parts}" if parts else "")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def decision_info(
    decision: str, *, reason: str | None = None, cache_key: str | None, inputs: CacheKeyInputs | None = None,
    version: Any = None, cached_file_hash: str | None = None, current: SlicerFingerprint | None = None,
    stale: bool | None = None, stale_reasons: list[str] | None = None, policy: str | None = None,
    previous: dict | None = None,
) -> dict:
    """The `jobs.slice_cache_info` record for a dispatch decision (`hit` | `miss`). Keeps a prior `save` outcome."""
    info: dict = {
        "decision": decision,
        "reason": reason,
        "at": now_iso(),
        "cache_key": cache_key,
        "source_content_hash": inputs.source_content_hash if inputs else None,
        "sliced_version_id": getattr(version, "id", None),
        "cached_file_id": getattr(version, "file_id", None),
        "cached_file_hash": cached_file_hash,
        "preset_content_hash_stored": getattr(version, "preset_content_hash", None),
        "preset_content_hash_current": current.preset_content_hash if current else None,
        "slicer_version_stored": getattr(version, "slicer_version", None),
        "slicer_version_current": current.slicer_version if current else None,
        "stale": stale,
        "stale_reasons": stale_reasons or [],
        "policy": policy,
    }
    if previous and previous.get("save"):
        info["save"] = previous["save"]
    return info


def with_save_outcome(
    previous: dict | None, outcome: str, *, cache_key: str | None, sliced_version_id: int | None = None,
    file_id: int | None = None, error: str | None = None,
) -> dict:
    """`jobs.slice_cache_info` with its `save` sub-record (saved | duplicate | failed) set; other keys kept."""
    info = dict(previous or {})
    info["save"] = {
        "outcome": outcome, "sliced_version_id": sliced_version_id, "cache_key": cache_key,
        "file_id": file_id, "error": error, "at": now_iso(),
    }
    return info


def policy_name(use_latest_settings: bool) -> str:
    return "use_latest" if use_latest_settings else "pin_cached"
