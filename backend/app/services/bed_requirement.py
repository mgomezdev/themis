"""The same stored footprint excludes small printers at target expansion and claim time."""
from pydantic import BaseModel, ConfigDict, Field


class MinBed(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(gt=0)
    y: float = Field(gt=0)
    z: float | None = Field(default=None, ge=0)


async def reason(session, requirement, printer):
    if not requirement:
        return None
    from .project_review import bed_height, fits
    size = (requirement["x"], requirement["y"], requirement.get("z") or 0)
    if fits(size, printer, await bed_height(session, printer)):
        return None
    return f"{printer.name}: bed too small; plate requires {size[0]:g} x {size[1]:g} x {size[2]:g} mm"


def largest(items):
    requirements = [i.min_bed_mm for i in items if i.min_bed_mm]
    if not requirements:
        return None
    return {axis: max(r.get(axis) or 0 for r in requirements) for axis in ("x", "y", "z")}
