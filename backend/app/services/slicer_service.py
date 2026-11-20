from __future__ import annotations
import contextlib
import dataclasses
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_data_dir
from .artifact_store import ArtifactError, ArtifactNotFound
from .library_store import get_library_store
from .print_artifacts import SUFFIXES, model_input
from .providers.contract import (
    ArtifactKind, CancelToken, ErrorCode, FilamentChoice, ProduceRequest, ProviderError, SliceSettings, TargetPrinter,
)
from .providers.slicing import SlicingProvider, default_provider_id, gcode_producer_for
from .three_mf_parser import plate_thumbnail_bytes

logger = logging.getLogger(__name__)

class SliceError(Exception):
    pass


class ProfileCatalogUnavailable(SliceError):
    """The preset catalog could not be read (the slicing service is unreachable), so names can't be resolved."""


class SliceCancelled(SliceError):
    """The slice was cancelled (job cancelled / printer stopped) while it ran. Not a failure: never marks the printer config."""


DEFAULT_BED_MM = (256.0, 256.0, 250.0)   # the printer row's default bed; produce() does not place by bed


@dataclass
class SliceRequest:
    """What a single (job, printer) slice needs.

    ``machine_preset`` is the printer's resolved machine profile (model mapping + nozzle variant, BIZ-294);
    ``process_preset`` and ``filament_presets`` are names understood by the selected provider.
    ``output_kind`` comes from the printer's declared file requirements.

    The source model is either a local path (``source_3mf``) or, preferably, a library
    artifact key (``source_key``): ``SlicerService.slice`` then materializes that artifact
    for the duration of the slice and ``source_3mf`` is ignored (BIZ-287).
    """
    job_id: int
    source_3mf: str
    plate_number: int
    machine_preset: str
    process_preset: str
    filament_presets: list[str]
    filament_colours: list[str] = field(default_factory=list)
    extra_config: dict = field(default_factory=dict)
    source_key: "str | None" = None
    # The `gcode.produce` plugin to slice with (BIZ-291: the one the queue resolved for this printer). None = the capability default.
    provider_id: "str | None" = None
    # Slice-side tool routing (BIZ-295): set only for a printer whose client needs its 3MF remapped (`slice_side_mapping`); the
    # producer applies it to a job-scoped copy. `filament_map` is `[{model_filament (1-based), tool_index (0-based)}]`.
    tool_index: "int | None" = None
    filament_map: "list[dict] | None" = None
    bed_mm: "tuple[float, float, float]" = DEFAULT_BED_MM
    # The printer's own curated overrides (lowest precedence; the provider puts the project file's changes and then
    # `extra_config`'s job overrides over them).
    printer_overrides: dict = field(default_factory=dict)
    output_kind: ArtifactKind = ArtifactKind.GCODE
    artifact_basename: str | None = None
    model_uuid: str | None = None


def slice_side_mapping(client, tool_index, filament_map) -> "tuple[int | None, list | None]":
    """The (tool_index, filament_map) a slice must apply to the 3MF, or (None, None). Only a printer whose client declares
    `slice_tool_mapping` has its 3MF rewritten (Bambu realizes the mapping at print time); the rewrite itself is the slicing
    provider's (inside `produce()`), never the printer client's."""
    if client is None or not getattr(client, "slice_tool_mapping", False):
        return None, None
    return tool_index, (filament_map or None)


def resolve_preset_uuids(
    machine_preset: str, process_preset: str, filament_presets: list[str], provider: SlicingProvider,
) -> "tuple[str, str, list[str]]":
    """Look up preset refs (Laminus: profile UUIDs) from the provider's catalog by name.

    Returns (machine_uuid, process_uuid, [filament_uuid, ...]). Raises SliceError
    naming the specific preset(s) and kind that didn't resolve, so the user knows
    exactly what to fix instead of re-checking three presets at once.
    """
    # Prefer the Themis-side catalog cache (populated at boot) over a fresh
    # sidecar call. Falls back to a direct fetch only if not yet warmed.
    from . import catalog_service
    catalog = catalog_service.cached_catalog(provider)
    if catalog is None:
        try:
            catalog = provider.get_catalog()
            catalog_service.remember_catalog(catalog, provider)
        except Exception as exc:
            logger.warning("Could not fetch sidecar catalog: %s", exc)
            raise ProfileCatalogUnavailable(f"Laminus sidecar unreachable — cannot resolve profiles: {exc}") from exc
    machine_uuid = catalog.ref_for("machine", machine_preset)
    process_uuid = catalog.ref_for("process", process_preset)

    # Name every unresolved preset and its kind — not just "something didn't match".
    missing: list[str] = []
    if not machine_uuid:
        missing.append(f"machine preset {machine_preset!r}")
    if not process_uuid:
        missing.append(f"process preset {process_preset!r}")

    filament_uuids = []
    missing_filaments: list[str] = []
    for name in filament_presets:
        fid = catalog.ref_for("filament", name)
        if fid:
            filament_uuids.append(fid)
        else:
            missing_filaments.append(name)
    if not filament_presets:
        missing.append("no filament preset was supplied")
    elif missing_filaments:
        missing.append(f"filament preset(s) {missing_filaments!r}")

    if missing:
        detail = "; ".join(missing)
        logger.warning("Sidecar UUID miss — %s", detail)
        raise SliceError(
            f"{detail} — not found in Laminus sidecar catalog. Refresh the profile "
            f"sync from Laminus so the picker offers valid choices, then re-select "
            f"the affected preset(s)."
        )

    return machine_uuid, process_uuid, filament_uuids


def _settings_of(req: SliceRequest) -> SliceSettings:
    """The neutral settings for `req`: the plate type and the curated overrides travel separately (the provider maps both)."""
    extra = dict(req.extra_config or {})
    plate_type = extra.pop("curr_bed_type", None)
    colours = req.filament_colours or []
    return SliceSettings(
        process_profile=req.process_preset or None,
        filaments=tuple(FilamentChoice(i + 1, "", profile=name, color_hex=colours[i] if i < len(colours) else None)
                        for i, name in enumerate(req.filament_presets)),
        overrides=extra,
        tool_index=req.tool_index,
        filament_map=tuple((int(m["model_filament"]), int(m["tool_index"])) for m in req.filament_map) if req.filament_map else None,
        plate_type=plate_type,
        printer_overrides=dict(req.printer_overrides or {}),
    )


class SlicerService:
    def __init__(self, data_dir: str | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir else get_data_dir()

    # ── public API ────────────────────────────────────────────────────────────
    def slice(self, req: SliceRequest, output_dir: "Path | None" = None, cancel: "CancelToken | None" = None) -> str:
        """Slice `req` with its `gcode.produce` provider's neutral `produce()` (BIZ-295) and return the artifact path. The
        provider resolves profile names, overrides and tool mapping; this only translates the request and adds the plate
        thumbnail to raw G-code. Raises SliceError (the provider's message, unchanged) or SliceCancelled when `cancel` was set.

        ``output_dir`` overrides the default gcode directory (``<data_dir>/gcode/<job_id>``), e.g. to isolate estimate gcode from
        production gcode. The directory is created if it does not exist.
        """
        pid = req.provider_id or default_provider_id()
        if pid is None:
            raise SliceError("No slicing provider is configured")
        producer = gcode_producer_for(pid)
        if producer is None:
            from ..plugins import get_plugin
            plugin = get_plugin(pid)
            if plugin is None:
                raise SliceError(f"Slicing provider {pid} is unavailable")
            label = plugin.name
            raise SliceError(f"{label} is not configured — set its URL in Settings -> {label}; it is required for slicing")

        if req.source_key is not None:
            # The provider reads a local path: hold a materialized copy of the library artifact for the whole slice.
            with contextlib.ExitStack() as held:
                try:   # only opening the artifact maps to a SliceError; the slice's own errors pass through untouched
                    local = held.enter_context(get_library_store().materialize(req.source_key))
                except ArtifactNotFound as exc:
                    raise SliceError(f"the source model {req.source_key!r} is missing from the library") from exc
                except ArtifactError as exc:
                    raise SliceError(f"could not read the source model {req.source_key!r}: {exc}") from exc
                return self.slice(dataclasses.replace(req, source_key=None, source_3mf=str(local), provider_id=pid), output_dir, cancel)

        out_dir = output_dir if output_dir is not None else (self._data_dir / "gcode" / str(req.job_id))
        out_dir.mkdir(parents=True, exist_ok=True)
        for stale in (*out_dir.glob("*.gcode"), *out_dir.glob("*.gcode.3mf")):
            stale.unlink(missing_ok=True)

        source = Path(req.source_3mf)
        kind = req.output_kind
        request = ProduceRequest(
            source=source, input_kind=model_input(source.name), plate=req.plate_number,
            target=TargetPrinter(req.bed_mm, model_uuid=req.model_uuid, machine_profile=req.machine_preset or None),
            settings=_settings_of(req), want=kind,
        )
        if request.input_kind not in producer.accepted_inputs or kind not in producer.output_kinds:
            raise SliceError(f"{pid} cannot convert {request.input_kind.value} to {SUFFIXES[kind]}")
        try:
            artifact = producer.produce(request, out_dir, cancel=cancel)
        except ProviderError as e:
            if e.code is ErrorCode.CANCELLED:
                raise SliceCancelled(e.message or "the slice was cancelled") from e
            raise SliceError(e.message or str(e)) from e

        suffix = SUFFIXES[kind]
        if artifact.kind != kind or not artifact.path.name.lower().endswith(suffix):
            raise SliceError(f"{pid} did not return the requested {suffix} artifact")
        result = str(artifact.path)
        if req.artifact_basename and kind is ArtifactKind.GCODE_3MF:
            result = str(artifact.path.rename(out_dir / f"{req.artifact_basename}{suffix}"))
        if kind is ArtifactKind.GCODE and source.suffix.lower() == ".3mf":
            self._inject_thumbnail(result, req.source_3mf, req.plate_number)
        return result

    # ── internals ─────────────────────────────────────────────────────────────
    def _inject_thumbnail(self, gcode_path: str, source_3mf: str, plate_number: int) -> None:
        """Extract the plate thumbnail from the source 3MF and prepend it to the
        gcode as base64 comments so Elegoo/Snapmaker screens can display a preview."""
        import base64
        try:
            with zipfile.ZipFile(source_3mf, "r") as z:
                thumb_data = plate_thumbnail_bytes(z, plate_number)
            if thumb_data is None:
                return

            if thumb_data[:8] != b"\x89PNG\r\n\x1a\n":
                return

            width = int.from_bytes(thumb_data[16:20], byteorder="big")
            height = int.from_bytes(thumb_data[20:24], byteorder="big")
            encoded = base64.b64encode(thumb_data).decode("ascii")
            chunks = [encoded[i:i+78] for i in range(0, len(encoded), 78)]

            buf = [f"; thumbnail begin {width}x{height} {len(thumb_data)}"]
            for chunk in chunks:
                buf.append(f"; {chunk}")
            buf.append("; thumbnail end")
            buf.append("\n")

            with open(gcode_path, "rb") as f:
                content = f.read()
            with open(gcode_path, "wb") as f:
                f.write("\n".join(buf).encode("utf-8"))
                f.write(content)

        except Exception as e:
            logger.warning("Failed to inject thumbnail into %s: %s", gcode_path, e)
