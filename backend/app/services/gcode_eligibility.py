"""Machine eligibility for pre-sliced G-code (BIZ-263).

A raw `.gcode` / `.gcode.3mf` was produced for one machine; sending it to another can damage the printer. Eligibility is a set of
core printer-model UUIDs (BIZ-262), never free text:

* **known** (`UploadedFile.eligibility_known`): `file_machine_eligibility` is the complete set. Set by hand on upload / file detail,
  or recorded when Themis saves a slice (the model it was sliced for + only the *registry-declared* equivalents of it).
* **unknown** (legacy files): never treated as a confirmed match. A job for it needs the user's explicit confirmation
  (`jobs.eligibility_confirmed`); without it every printer is refused with an explicit outcome.

`evaluate` is the single decision used at job creation (each target printer independently), by target materialisation, and at
queue pull; its `Outcome.code` / `message` are what the user sees. A model file (3MF/STL) is `not_applicable`: the slicer targets
the printer."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import FileMachineEligibility, Printer, PrinterModelRecord, UploadedFile
from ..plugins import get_plugin
from .library_scanner import is_presliced_file

SOURCES = ("manual", "target", "equivalent", "backfill")


@dataclass(frozen=True)
class Outcome:
    allowed: bool
    # not_applicable | eligible | equivalent | unknown_confirmed | unknown_eligibility | incompatible | printer_model_unknown
    code: str
    message: str = ""


async def equivalents_of(session: AsyncSession, model_uuid: str) -> set[str]:
    """UUIDs of the models the registry *declares* equivalent to `model_uuid` (symmetric, same plugin). Nothing is inferred."""
    row = await session.get(PrinterModelRecord, model_uuid)
    manifest = get_plugin(row.plugin_id) if row else None
    if row is None or manifest is None:
        return set()
    declared: dict[str, set[str]] = {}
    for mfr in manifest.manufacturers:
        for m in mfr.models:
            for eq in m.equivalents:
                declared.setdefault(m.id, set()).add(eq)
                declared.setdefault(eq, set()).add(m.id)
    ids = declared.get(row.model_id, set())
    if not ids:
        return set()
    found = (await session.execute(select(PrinterModelRecord.id).where(
        PrinterModelRecord.plugin_id == row.plugin_id, PrinterModelRecord.model_id.in_(ids)))).scalars().all()
    return set(found)


async def eligibility_of(session: AsyncSession, file_id: int) -> dict:
    """`{known, models: [{model_uuid, source, display_name, manufacturer_name, dormant}]}`; unknown files list no models."""
    f = await session.get(UploadedFile, file_id)
    rows = (await session.execute(
        select(FileMachineEligibility, PrinterModelRecord)
        .join(PrinterModelRecord, PrinterModelRecord.id == FileMachineEligibility.model_uuid)
        .where(FileMachineEligibility.file_id == file_id)
        .order_by(PrinterModelRecord.manufacturer_name, PrinterModelRecord.display_name))).all()
    return {"known": bool(f and f.eligibility_known),
            "models": [{"model_uuid": r.model_uuid, "source": r.source, "display_name": m.display_name,
                        "manufacturer_name": m.manufacturer_name} for r, m in rows]}


async def eligibility_for_files(session: AsyncSession, file_ids: list[int]) -> dict[int, list[str]]:
    """Model UUIDs per file in one query (list routes must not query per file); every id gets a key."""
    out: dict[int, list[str]] = {i: [] for i in file_ids}
    if file_ids:
        for fid, uid in (await session.execute(select(FileMachineEligibility.file_id, FileMachineEligibility.model_uuid)
                                                .where(FileMachineEligibility.file_id.in_(file_ids))
                                                .order_by(FileMachineEligibility.id))).all():
            out[fid].append(uid)
    return out


async def set_eligibility(session: AsyncSession, file: UploadedFile, model_uuids: list[str], *, source: str = "manual") -> None:
    """Replace the file's eligibility with exactly `model_uuids` (a known set; empty = explicitly no machine). Caller commits.
    Raises ValueError for an unknown model UUID or a file that is not pre-sliced."""
    if not is_presliced_file(file):
        raise ValueError("Only pre-sliced G-code files carry machine eligibility")
    wanted = list(dict.fromkeys(model_uuids))
    known = set((await session.execute(select(PrinterModelRecord.id).where(PrinterModelRecord.id.in_(wanted)))).scalars()) if wanted else set()
    missing = [u for u in wanted if u not in known]
    if missing:
        raise ValueError(f"Unknown printer model(s): {', '.join(missing)}")
    await session.execute(delete(FileMachineEligibility).where(FileMachineEligibility.file_id == file.id))
    for uid in wanted:
        session.add(FileMachineEligibility(file_id=file.id, model_uuid=uid, source=source))
    file.eligibility_known = True
    await session.flush()


async def record_slice_target(session: AsyncSession, file: UploadedFile, target_model_uuid: str | None) -> bool:
    """A saved slice is eligible for the model it was sliced for plus that model's registry-declared equivalents. A printer with
    no registered model leaves the file unknown (returns False) rather than guessing. Caller commits."""
    if target_model_uuid is None:
        return False
    await session.execute(delete(FileMachineEligibility).where(FileMachineEligibility.file_id == file.id))
    session.add(FileMachineEligibility(file_id=file.id, model_uuid=target_model_uuid, source="target"))
    for uid in sorted(await equivalents_of(session, target_model_uuid) - {target_model_uuid}):
        session.add(FileMachineEligibility(file_id=file.id, model_uuid=uid, source="equivalent"))
    file.eligibility_known = True
    await session.flush()
    return True


async def evaluate(session: AsyncSession, file: UploadedFile | None, printer: Printer, *, confirmed: bool = False) -> Outcome:
    if file is None or not is_presliced_file(file):
        return Outcome(True, "not_applicable")
    if not file.eligibility_known:
        if confirmed:
            return Outcome(True, "unknown_confirmed")
        return Outcome(False, "unknown_eligibility",
                       f"{file.original_filename} has no recorded machine eligibility — confirm it fits {printer.name}, "
                       "or set the printer models it is for")
    row = (await session.execute(select(FileMachineEligibility).where(
        FileMachineEligibility.file_id == file.id, FileMachineEligibility.model_uuid == printer.model_uuid))).scalar_one_or_none() \
        if printer.model_uuid else None
    if row is not None:
        return Outcome(True, "equivalent" if row.source == "equivalent" else "eligible")
    if printer.model_uuid is None:
        return Outcome(False, "printer_model_unknown",
                       f"{printer.name} has no registered printer model, so {file.original_filename} cannot be checked against it")
    names = (await session.execute(select(PrinterModelRecord.manufacturer_name, PrinterModelRecord.display_name)
                                   .join(FileMachineEligibility, FileMachineEligibility.model_uuid == PrinterModelRecord.id)
                                   .where(FileMachineEligibility.file_id == file.id))).all()
    fits = ", ".join(f"{m} {d}" for m, d in names) or "no printer model"
    return Outcome(False, "incompatible", f"{printer.name} can't take {file.original_filename}: it is for {fits}")
