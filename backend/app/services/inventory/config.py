"""`inventory_config` (singleton): deduct_on_complete + low-stock thresholds. Provider-scoped keys are namespaced
`"<provider>:<ref>"`, so switching providers keeps each one's thresholds and alert state."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ...models import InventoryConfig


def ns(provider_id: str, ref: str) -> str:
    return f"{provider_id}:{ref}"


def split_ns(key: str) -> tuple[str, str]:
    provider, _, ref = key.partition(":")
    return provider, ref


async def get_config(session: AsyncSession) -> InventoryConfig:
    row = await session.get(InventoryConfig, 1)
    if row is None:
        row = InventoryConfig(id=1, deduct_on_complete=True, low_stock_overrides={}, low_stock_alerted=[])
        session.add(row)
        await session.flush()
    return row


def overrides_for(row: InventoryConfig | None, provider_id: str) -> dict[str, float]:
    """The active provider's per-material thresholds, keyed by plain material ref."""
    out: dict[str, float] = {}
    for key, grams in ((row.low_stock_overrides or {}) if row else {}).items():
        provider, ref = split_ns(key)
        if provider == provider_id:
            out[ref] = float(grams)
    return out


def merge_overrides(row: InventoryConfig, provider_id: str, overrides: dict[str, float]) -> dict[str, float]:
    """Replace the active provider's entries, leaving every other provider's untouched."""
    kept = {k: v for k, v in (row.low_stock_overrides or {}).items() if split_ns(k)[0] != provider_id}
    return {**kept, **{ns(provider_id, ref): float(g) for ref, g in overrides.items()}}


async def deduct_enabled(session: AsyncSession) -> bool:
    """The `deduct_on_complete` setting (default on, which is what always happened before it existed)."""
    row = await session.get(InventoryConfig, 1)
    return True if row is None else bool(row.deduct_on_complete)
