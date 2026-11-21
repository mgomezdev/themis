"""Neutral, validated per-device topology. No protocol or manufacturer assumptions."""
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Tool(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str = Field(min_length=1)
    nozzle_mm: float = Field(gt=0, allow_inf_nan=False)


class Position(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str = Field(min_length=1)
    filament: dict | None = None
    reported: bool = False


class PrinterTopology(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    tools: tuple[Tool, ...] = Field(min_length=1)
    positions: tuple[Position, ...] = Field(min_length=1)
    swapping: bool

    @model_validator(mode='after')
    def valid_topology(self):
        if len(self.positions) < len(self.tools):
            raise ValueError('configured filament positions must be >= toolheads')
        if len({t.id for t in self.tools}) != len(self.tools) or len({p.id for p in self.positions}) != len(self.positions):
            raise ValueError('tool and position identities must be unique')
        if len(self.positions) > 1 and not self.swapping:
            raise ValueError('multiple configured positions require swapping support')
        return self
