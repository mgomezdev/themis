from __future__ import annotations
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import get_data_dir

logger = logging.getLogger(__name__)

_EXPORT_3MF = "--export-3mf"


class SliceError(Exception):
    pass


def _export_3mf_name(export_args: list[str]) -> str | None:
    """Return the --export-3mf filename from export_args, or None for raw gcode."""
    try:
        return export_args[export_args.index(_EXPORT_3MF) + 1]
    except (ValueError, IndexError):
        return None


@dataclass
class SliceRequest:
    """What a single (job, printer) slice needs.

    ``machine_preset`` is the printer's ``current_orca_printer_profile``;
    ``process_preset``/``filament_presets`` are OrcaSlicer preset names resolved
    by the Orca sidecar catalog. ``export_args`` are the printer-specific output
    args: ``[]`` yields raw gcode, ``["--export-3mf", "<name>.gcode.3mf"]`` yields
    the archive. ``extra_config`` carries runtime overrides (bed type, job-level
    setting overrides) merged by the sidecar after profile resolution.
    """
    job_id: int
    source_3mf: str
    plate_number: int
    machine_preset: str
    process_preset: str
    filament_presets: list[str]
    filament_colours: list[str] = field(default_factory=list)
    export_args: list[str] = field(default_factory=list)
    prepare_hook: "Callable[[Path], None] | None" = None
    extra_config: dict = field(default_factory=dict)


class SlicerService:
    def __init__(self, data_dir: str | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir else get_data_dir()

    # ── public API ────────────────────────────────────────────────────────────
    def slice(self, req: SliceRequest, output_dir: "Path | None" = None) -> str:
        """Resolve profile names to sidecar UUIDs and delegate the full slice to
        the Orca sidecar (POST /api/slice/start). The sidecar owns all profile
        resolution, 3MF assembly, and gcode generation. Raises SliceError if the
        sidecar is unreachable, unconfigured, or any profile is not in its catalog.

        ``output_dir`` overrides the default gcode directory
        (``<data_dir>/gcode/<job_id>``). Callers can use this to isolate estimate
        gcode from production gcode. The directory is created if it does not exist.
        """
        from ..config import get_laminus_sidecar_url
        sidecar_url = get_laminus_sidecar_url()
        if not sidecar_url:
            raise SliceError("LAMINUS_SIDECAR_URL is not configured — Laminus sidecar is required for slicing")

        if req.prepare_hook is not None:
            raise SliceError(
                "Multi-extruder remapping via prepare_hook is not supported in sidecar-only mode"
            )

        out_dir = output_dir if output_dir is not None else (self._data_dir / "gcode" / str(req.job_id))
        out_dir.mkdir(parents=True, exist_ok=True)

        uuids = self._resolve_uuids(req, sidecar_url)
        if uuids is None:
            raise SliceError(
                f"Profile not found in Laminus sidecar catalog — "
                f"machine={req.machine_preset!r} process={req.process_preset!r} "
                f"filaments={req.filament_presets!r}"
            )
        machine_uuid, process_uuid, filament_uuids = uuids
        return self._execute_slice_by_ids(
            req, machine_uuid, process_uuid, filament_uuids, out_dir, sidecar_url
        )

    # ── internals ─────────────────────────────────────────────────────────────
    def _resolve_uuids(
        self,
        req: SliceRequest,
        sidecar_url: str,
    ) -> "tuple[str, str, list[str]] | None":
        """Look up profile UUIDs from the sidecar catalog by name.

        Returns (machine_uuid, process_uuid, [filament_uuid, ...]) or None if
        any name is absent — caller raises SliceError.
        """
        # Prefer the Themis-side catalog cache (populated at boot) over a fresh
        # sidecar call. Falls back to a direct fetch only if not yet warmed.
        from ..api.routes import laminus as _laminus_module
        catalog = _laminus_module._catalog_dict
        if catalog is None:
            try:
                from .laminus_sidecar_client import LaminusSidecarClient
                catalog = LaminusSidecarClient(sidecar_url).get_catalog()
                _laminus_module._catalog_dict = catalog
            except Exception as exc:
                logger.warning("Could not fetch sidecar catalog: %s", exc)
                raise SliceError(f"Laminus sidecar unreachable — cannot resolve profiles: {exc}") from exc
        machine_map = {m["name"]: m["uuid"] for m in catalog.get("machine", [])}
        process_map = {p["name"]: p["uuid"] for p in catalog.get("process", [])}
        filament_map = {f["name"]: f["uuid"] for f in catalog.get("filament", [])}

        machine_uuid = machine_map.get(req.machine_preset)
        process_uuid = process_map.get(req.process_preset)
        if not machine_uuid or not process_uuid:
            logger.warning(
                "Sidecar UUID miss — machine=%r found=%s, process=%r found=%s",
                req.machine_preset, bool(machine_uuid),
                req.process_preset, bool(process_uuid),
            )
            return None

        filament_uuids = []
        for name in req.filament_presets:
            fid = filament_map.get(name)
            if not fid:
                logger.warning("Sidecar UUID miss — filament=%r not in catalog", name)
                return None
            filament_uuids.append(fid)

        if not filament_uuids:
            return None

        return machine_uuid, process_uuid, filament_uuids

    def _execute_slice_by_ids(
        self,
        req: SliceRequest,
        machine_uuid: str,
        process_uuid: str,
        filament_uuids: list[str],
        out_dir: Path,
        sidecar_url: str,
    ) -> str:
        """Delegate the full slice to the sidecar using stable profile UUIDs.

        The sidecar resolves inheritance, builds the 3MF with extra_config merged
        on top, slices, and streams the artifact back. No local file access.
        """
        from .laminus_sidecar_client import LaminusSidecarClient, SidecarError
        client = LaminusSidecarClient(sidecar_url)
        export_3mf = _export_3mf_name(req.export_args) is not None
        source = Path(req.source_3mf)
        for stale in (*out_dir.glob("*.gcode"), *out_dir.glob("*.gcode.3mf")):
            stale.unlink(missing_ok=True)
        try:
            job_id = client.slice_start(
                source, machine_uuid, process_uuid, filament_uuids,
                req.plate_number, export_3mf=export_3mf,
                extra_config=req.extra_config or None,
            )
            status = client.poll_status(job_id)
            dest = out_dir / status["sliced_file"]
            result = str(client.download(job_id, dest))
        except SidecarError as e:
            raise SliceError(str(e)) from e

        if not export_3mf and source.suffix.lower() == ".3mf":
            self._inject_thumbnail(result, req.source_3mf, req.plate_number)
        return result

    def _inject_thumbnail(self, gcode_path: str, source_3mf: str, plate_number: int) -> None:
        """Extract the plate thumbnail from the source 3MF and prepend it to the
        gcode as base64 comments so Elegoo/Snapmaker screens can display a preview."""
        import base64
        try:
            with zipfile.ZipFile(source_3mf, "r") as z:
                names = z.namelist()
                thumb_path = f"Metadata/plate_{plate_number}.png"
                if thumb_path not in names:
                    if "Metadata/thumbnail.png" in names:
                        thumb_path = "Metadata/thumbnail.png"
                    elif "Metadata/preview.png" in names:
                        thumb_path = "Metadata/preview.png"
                    else:
                        return
                thumb_data = z.read(thumb_path)

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
