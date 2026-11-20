"""Model -> slicer machine profile resolution (BIZ-294).

A printer's slicer machine profile is NOT stored per printer. It is found by (printer model, slicing provider, nozzle variant):

    raw per-printer override  (`Printer.machine_profile_override`, advanced; wins when set)
    else  the mapping row of the printer's model for the provider that will slice it, for the printer's `nozzle_variant`
          (NULL variant = the model's default row)

No mapping for the printer's variant means the provider is ineligible for that printer: the reason ("no machine profile for <model>
<variant>") is the job's block reason and feeds `preference_ineligible` / `default_ineligible` (services/slice_resolution.py).
Pure helpers + small async loaders; nothing here names a slicer or a vendor. Profile NAMES are provider-specific, hence the per-provider key."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import BrandConversionPreference, PrintConversionDefault, Printer, PrinterModelRecord, PrinterModelSlicerProfile
from .print_artifacts import model_input, required_output
from .providers.slicing import effective_provider_id

FLOWS = ("standard", "high_flow")


@dataclass(frozen=True)
class NozzleVariant:
    """The physical nozzle a profile is for: diameter and flow (standard / high-flow). Hardened steel does not change the machine
    preset (filament gating only), so it is not part of the variant."""
    diameter_mm: float
    flow: str = "standard"

    @property
    def label(self) -> str:
        d = f"{self.diameter_mm:g}mm"
        return f"{d} high-flow" if self.flow == "high_flow" else d

    def as_dict(self) -> dict:
        return {"diameter_mm": self.diameter_mm, "flow": self.flow}

    @classmethod
    def parse(cls, raw: object) -> "NozzleVariant | None":
        """From a stored/posted `{diameter_mm, flow}`; None for NULL or anything malformed (treated as 'the default')."""
        if not isinstance(raw, dict):
            return None
        try:
            d = float(raw.get("diameter_mm"))
        except (TypeError, ValueError):
            return None
        flow = raw.get("flow") or "standard"
        return cls(d, flow) if d > 0 and flow in FLOWS else None


def variant_of(row: PrinterModelSlicerProfile) -> NozzleVariant:
    return NozzleVariant(float(row.nozzle_diameter_mm), row.flow)


@dataclass(frozen=True)
class MachineProfileResolution:
    """`profile` is the machine profile NAME to slice with, or None with `reason` saying why (shown as the job's block reason)."""
    profile: str | None
    reason: str | None = None
    provider_id: str | None = None
    from_override: bool = False
    model_label: str | None = None      # "<manufacturer> <model>" of the printer's registry model, for pickers ("Any Bambu Lab P1S")
    model_provider_id: str | None = None
    brand_provider_id: str | None = None
    conversion_default_id: str | None = None

    def preferred_provider_id(self, printer: Printer) -> str | None:
        return printer.preferred_slicer_plugin_id or self.model_provider_id or self.brand_provider_id


def _no_profile(model_name: str | None, variant: NozzleVariant | None, provider_id: str | None) -> MachineProfileResolution:
    if model_name is None:
        return MachineProfileResolution(None, "no machine profile for this printer (it has no registered model and no profile "
                                              "override)", provider_id)
    what = variant.label if variant else "default nozzle"
    return MachineProfileResolution(None, f"no machine profile for {model_name} {what}", provider_id)


def resolve_from_rows(
    *, override: str | None, model_uuid: str | None, model_name: str | None, nozzle_variant: object,
    provider_id: str | None, rows: Iterable[PrinterModelSlicerProfile], model_label: str | None = None,
) -> MachineProfileResolution:
    """The pure resolver: `rows` are the mapping rows of this printer's model (any provider)."""
    return replace(_resolve(override, model_uuid, model_name, nozzle_variant, provider_id, rows), model_label=model_label)


def _resolve(override, model_uuid, model_name, nozzle_variant, provider_id, rows) -> MachineProfileResolution:
    if override:
        return MachineProfileResolution(override, None, provider_id, from_override=True)
    variant = NozzleVariant.parse(nozzle_variant)
    if not model_uuid or not provider_id:
        return _no_profile(model_name if model_uuid else None, variant, provider_id)
    mine = [r for r in rows if r.model_uuid == model_uuid and r.provider_plugin_id == provider_id]
    if variant is None:
        row = next((r for r in mine if r.is_default), None)
    else:
        row = next((r for r in mine if variant_of(r) == variant), None)
    if row is None:
        return _no_profile(model_name, variant, provider_id)
    return MachineProfileResolution(row.machine_profile, None, provider_id)


def _label(rec: PrinterModelRecord) -> str:
    return f"{rec.manufacturer_name} {rec.display_name or rec.model_id}".strip()


async def model_display_name(session: AsyncSession, model_uuid: str | None) -> str | None:
    if not model_uuid:
        return None
    rec = await session.get(PrinterModelRecord, model_uuid)
    return (rec.display_name or rec.model_id) if rec else model_uuid


async def mapping_rows(session: AsyncSession, model_uuid: str | None = None) -> list[PrinterModelSlicerProfile]:
    q = select(PrinterModelSlicerProfile).order_by(PrinterModelSlicerProfile.id)
    if model_uuid is not None:
        q = q.where(PrinterModelSlicerProfile.model_uuid == model_uuid)
    return list((await session.execute(q)).scalars().all())


async def resolve_for_printer(session: AsyncSession, printer: Printer, provider_id: str | None = ...,  # type: ignore[assignment]
                              *, source_filename: str | None = None) -> MachineProfileResolution:
    """Resolve the chosen service's profile. For a job, include the source filename to apply format-pair defaults."""
    rows = await mapping_rows(session, printer.model_uuid) if printer.model_uuid else []
    rec = await session.get(PrinterModelRecord, printer.model_uuid) if printer.model_uuid else None
    manufacturer_id = printer.manufacturer_id or (rec.manufacturer_id if rec else None)
    brand = await session.get(BrandConversionPreference, manufacturer_id) if manufacturer_id else None
    route = None
    if source_filename and provider_id is ... and not (printer.preferred_slicer_plugin_id or (rec and rec.default_slicer_plugin_id) or brand):
        route = await session.get(PrintConversionDefault, (model_input(source_filename), required_output(printer.printer_type)))
    return _resolve_printer(printer, rec, rows, provider_id, brand=brand, route=route)


def _resolve_printer(printer, model, rows, provider_id=..., *, brand=None, route=None) -> MachineProfileResolution:
    model_provider_id = model.default_slicer_plugin_id if model else None
    brand_provider_id = brand.provider_plugin_id if brand else None
    conversion_default_id = route.provider_plugin_id if route else None
    if provider_id is ...:
        provider_id = effective_provider_id(printer.preferred_slicer_plugin_id or model_provider_id or brand_provider_id or conversion_default_id)
    resolution = resolve_from_rows(
        override=printer.machine_profile_override, model_uuid=printer.model_uuid,
        model_name=(model.display_name or model.model_id) if model else printer.model_uuid,
        nozzle_variant=printer.nozzle_variant, provider_id=provider_id, rows=rows,
        model_label=_label(model) if model else None,
    )
    return replace(resolution, model_provider_id=model_provider_id, brand_provider_id=brand_provider_id,
                   conversion_default_id=conversion_default_id)


async def machine_profile_for(session: AsyncSession, printer: Printer | None) -> str | None:
    """Convenience: just the profile name (None when unresolved or no printer)."""
    return (await resolve_for_printer(session, printer)).profile if printer is not None else None


async def resolve_many(session: AsyncSession, printers: list[Printer]) -> dict[int, MachineProfileResolution]:
    """Resolution for many printers with two queries (list routes are polled; never per-printer)."""
    uuids = {p.model_uuid for p in printers if p.model_uuid}
    rows = [r for r in await mapping_rows(session) if r.model_uuid in uuids] if uuids else []
    recs = {}
    if uuids:
        recs = {rec.id: rec for rec in (await session.execute(
            select(PrinterModelRecord).where(PrinterModelRecord.id.in_(uuids)))).scalars()}
    brands = {p.id: p.manufacturer_id or (recs[p.model_uuid].manufacturer_id if p.model_uuid in recs else None)
              for p in printers}
    brand_ids = {brand for brand in brands.values() if brand}
    preferences = {r.manufacturer_id: r for r in (await session.execute(
        select(BrandConversionPreference).where(BrandConversionPreference.manufacturer_id.in_(brand_ids)))).scalars()} if brand_ids else {}
    return {p.id: _resolve_printer(p, recs.get(p.model_uuid), rows, brand=preferences.get(brands[p.id])) for p in printers}


async def profiles_of_model(session: AsyncSession, model_uuid: str) -> set[str]:
    """Every machine profile name mapped for the model (any provider/variant): what a cached slice made for the model may carry."""
    return {r.machine_profile for r in await mapping_rows(session, model_uuid)}
