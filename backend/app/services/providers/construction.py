"""Core access to `3mf.construct` (BIZ-296): the host's one active provider, as a neutral `ProjectConstructor`. Core names no
provider; callers gate on `contract.resolve_construct(get_project_constructor(), status)`."""
from __future__ import annotations

from .contract import CAP_3MF_CONSTRUCT, ProjectConstructor


def get_project_constructor() -> ProjectConstructor | None:
    """The active `3mf.construct` provider's constructor, or None (none selected/enabled, or the plugin is unconfigured)."""
    from ...plugins.host import plugin_host

    part = plugin_host.part(CAP_3MF_CONSTRUCT)
    return part.project_constructor() if part is not None else None
