"""Print groups (BIZ-360): every project has one Default Group; a group's targets say which printers take its jobs and with which
process profile; a fixed-bed group packs for one envelope. Target keys are the frontend's selection keys: `<printer id>`,
`model:<model uuid>`, `any`."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Printer, PrinterModelRecord, ProjectGroup, ProjectGroupTarget, ProjectItem

DEFAULT_GROUP_NAME = "Default Group"
ANY_KEY = "any"
MODEL_PREFIX = "model:"


async def ensure_default_group(session: AsyncSession, project_id: int) -> ProjectGroup:
    """The project's Default Group, created (holding every ungrouped item) when missing. Caller commits."""
    group = (await session.execute(select(ProjectGroup).where(
        ProjectGroup.project_id == project_id, ProjectGroup.is_default.is_(True)))).scalar_one_or_none()
    if group is None:
        group = ProjectGroup(project_id=project_id, name=DEFAULT_GROUP_NAME, sort_order=0, is_default=True)
        session.add(group)
        await session.flush()
    own = select(ProjectGroup.id).where(ProjectGroup.project_id == project_id)
    for item in (await session.execute(select(ProjectItem).where(
            ProjectItem.project_id == project_id,
            or_(ProjectItem.group_id.is_(None), ProjectItem.group_id.not_in(own))))).scalars():
        item.group_id = group.id          # ungrouped, or pointing at a group that no longer exists (group_id has no FK)
    return group


async def groups_of(session: AsyncSession, project_id: int) -> list[ProjectGroup]:
    await ensure_default_group(session, project_id)
    return list((await session.execute(select(ProjectGroup).where(ProjectGroup.project_id == project_id)
                                       .order_by(ProjectGroup.sort_order, ProjectGroup.id))).scalars().all())


async def targets_of(session: AsyncSession, group_id: int) -> list[ProjectGroupTarget]:
    return list((await session.execute(select(ProjectGroupTarget).where(ProjectGroupTarget.group_id == group_id)
                                       .order_by(ProjectGroupTarget.sort_order, ProjectGroupTarget.id))).scalars().all())


async def next_sort_order(session: AsyncSession, project_id: int) -> int:
    top = (await session.execute(select(func.max(ProjectGroup.sort_order)).where(ProjectGroup.project_id == project_id))).scalar()
    return (top or 0) + 1


async def check_target_key(session: AsyncSession, key: str) -> str | None:
    """Why `key` is not a valid target, or None."""
    if key == ANY_KEY:
        return None
    if key.startswith(MODEL_PREFIX):
        return None if await session.get(PrinterModelRecord, key[len(MODEL_PREFIX):]) else f"unknown printer model in {key!r}"
    if key.isascii() and key.isdigit():
        return None if await session.get(Printer, int(key)) else f"printer {key} does not exist"
    return f"{key!r} is not a printer id, 'model:<uuid>' or 'any'"


@dataclass
class ResolvedTargets:
    """What generation needs from a group's targets: explicit printers (bed-filtered) and model targets (None = Any printer), each
    with its process profile, plus every printer they cover now (for the auto envelope)."""
    printers: list[tuple[Printer, str]]
    models: list[tuple[str | None, str]]
    covered: list[Printer]


async def resolve(session: AsyncSession, targets: list[tuple[str, str]], envelope: tuple[float, float] | None) -> ResolvedTargets:
    """`targets` = [(target_key, process_profile)]. An explicit printer whose bed does not contain `envelope` is dropped (fixed-bed
    packing); model / Any targets are kept (a plate that doesn't fit a claiming printer fails its slice fit check, BIZ-355)."""
    all_printers = list((await session.execute(select(Printer).order_by(Printer.id))).scalars().all())
    printers: list[tuple[Printer, str]] = []
    models: list[tuple[str | None, str]] = []
    covered: dict[int, Printer] = {}
    for key, profile in targets:
        if key == ANY_KEY:
            models.append((None, profile))
            covered.update({p.id: p for p in all_printers})
        elif key.startswith(MODEL_PREFIX):
            uuid = key[len(MODEL_PREFIX):]
            models.append((uuid, profile))
            covered.update({p.id: p for p in all_printers if p.model_uuid == uuid})
        else:
            p = next((x for x in all_printers if x.id == int(key)), None)
            if p is None:
                continue
            covered[p.id] = p
            if envelope is None or bed_contains(p, envelope):
                printers.append((p, profile))
    return ResolvedTargets(printers, models, list(covered.values()))


def covers(key: str, printer: Printer) -> bool:
    """Does target `key` (`any`, `model:<uuid>`, `<printer id>`) take in `printer`?"""
    if key == ANY_KEY:
        return True
    if key.startswith(MODEL_PREFIX):
        return printer.model_uuid == key[len(MODEL_PREFIX):]
    return key == str(printer.id)


def bed_contains(printer: Printer, envelope: tuple[float, float]) -> bool:
    return float(printer.bed_x_mm) >= envelope[0] and float(printer.bed_y_mm) >= envelope[1]


def auto_envelope(printers: list[Printer]) -> tuple[float, float]:
    """The smallest bed among `printers` (what one fixed pack must fit), or the 256 mm default when there are none."""
    if not printers:
        return (256.0, 256.0)
    return (min(float(p.bed_x_mm) for p in printers), min(float(p.bed_y_mm) for p in printers))
