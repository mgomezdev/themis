"""Helpers for tests that need a primed/inspected catalog cache."""
from __future__ import annotations

import json

from app.services import catalog_service


def prime_catalog(raw: dict | None) -> None:
    """Install `raw` (legacy {"machine": [...], ...} dict) as the cached catalog; None clears the cache."""
    if raw is None:
        catalog_service._catalog = None
        return
    catalog_service.commit_catalog(json.dumps(raw).encode(), catalog_service.catalog_from_dict(raw))


def cached_raw() -> dict | None:
    return catalog_service.cached_raw()


def patch_cached_catalog(catalog):
    """Context manager: `catalog_service.get_cached_catalog` returns `catalog` (a legacy dict / Catalog) or,
    when given an Exception, raises it."""
    from unittest.mock import AsyncMock, patch

    if isinstance(catalog, Exception):
        mock = AsyncMock(side_effect=catalog)
    else:
        mock = AsyncMock(return_value=catalog_service.catalog_from_dict(catalog) if isinstance(catalog, dict) else catalog)
    return patch("app.services.catalog_service.get_cached_catalog", mock)
