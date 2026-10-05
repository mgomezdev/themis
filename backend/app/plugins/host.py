"""PluginHost: lifecycle, the active rule, and failure containment (spec §3.1).

* **Active rule (one source of truth):** a provider is active iff `extension_slots[kind] == id` AND
  `plugin_configs[id].enabled`. Core asks only `host.active(kind)` / `host.has(kind, cap)`.
* **Containment:** every core -> provider call goes through `host.call(...)`: a timeout, every exception caught,
  logged with the plugin id and recorded in `plugin_configs.state.last_error`; the caller gets a typed
  `CallResult`, never an exception. Plugin errors never reach the queue loop or a request handler.
* **No I/O on the queue loop:** the loop never awaits `call()`; it schedules via the host's own tasks / outbox
  (see the deduction model). `active()`/`has()` are synchronous and read an in-memory snapshot.
* A settings change or provider switch rebuilds the instance in place (no restart)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import ExtensionSlot, PluginConfig
from . import PluginError, get_plugin
from .manifest import PluginManifest

logger = logging.getLogger("app")

DEFAULT_TIMEOUT_S = 10.0


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


@dataclass(frozen=True)
class CallResult:
    ok: bool
    value: Any = None
    error: str | None = None
    # ok | inactive (no active provider / method) | timeout | error
    reason: Literal["ok", "inactive", "timeout", "error"] = "ok"


@dataclass(frozen=True)
class ActivePlugin:
    manifest: PluginManifest
    instance: Any


@dataclass
class _Snapshot:
    enabled: bool = False
    settings: dict = field(default_factory=dict)
    secrets: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)


class PluginHost:
    def __init__(self) -> None:
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._slots: dict[str, str | None] = {}
        self._configs: dict[str, _Snapshot] = {}
        self._instances: dict[str, Any] = {}
        self._build_errors: dict[str, str] = {}

    # --- lifecycle -------------------------------------------------------------------------------------------

    def configure(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def start(self) -> None:
        await self.reload()

    async def stop(self) -> None:
        for plugin_id in list(self._instances):
            await self._close(plugin_id)
        self._instances.clear()

    async def reload(self) -> None:
        """Re-read slots + configs from the DB and rebuild every active provider instance in place."""
        assert self._session_factory is not None, "PluginHost.configure() was not called"
        async with self._session_factory() as s:
            slots = {r.kind: r.plugin_id for r in (await s.execute(select(ExtensionSlot))).scalars()}
            configs = {r.plugin_id: _Snapshot(bool(r.enabled), dict(r.settings or {}), dict(r.secrets or {}),
                                              dict(r.state or {}))
                       for r in (await s.execute(select(PluginConfig))).scalars()}
        self._slots, self._configs = slots, configs
        wanted = {pid for pid in slots.values() if pid and self._is_enabled(pid)}
        for plugin_id in list(self._instances):
            if plugin_id not in wanted:
                await self._close(plugin_id)
                self._instances.pop(plugin_id, None)
        self._build_errors = {k: v for k, v in self._build_errors.items() if k in wanted}
        for plugin_id in sorted(wanted):
            await self._rebuild(plugin_id)

    def _is_enabled(self, plugin_id: str) -> bool:
        cfg = self._configs.get(plugin_id)
        return bool(cfg and cfg.enabled and get_plugin(plugin_id) is not None)

    async def _close(self, plugin_id: str) -> None:
        inst = self._instances.get(plugin_id)
        closer = getattr(inst, "aclose", None)
        if closer is not None:
            try:
                await closer()
            except Exception:
                logger.exception("Plugin %s failed to close", plugin_id)

    async def _rebuild(self, plugin_id: str) -> None:
        manifest, cfg = get_plugin(plugin_id), self._configs[plugin_id]
        await self._close(plugin_id)
        try:
            settings = manifest.settings_model(**{**cfg.settings, **cfg.secrets})
            self._instances[plugin_id] = manifest.factory(settings)
            self._build_errors.pop(plugin_id, None)
        except Exception as e:                          # contained: a bad config must not stop Themis
            logger.exception("Plugin %s could not be built", plugin_id)
            self._instances.pop(plugin_id, None)
            self._build_errors[plugin_id] = f"could not build provider: {e}"
            await self._record(plugin_id, last_error=self._build_errors[plugin_id])

    # --- the active rule ---------------------------------------------------------------------------------------

    def active(self, kind: str) -> ActivePlugin | None:
        plugin_id = self._slots.get(kind)
        if not plugin_id or not self._is_enabled(plugin_id):
            return None
        inst, manifest = self._instances.get(plugin_id), get_plugin(plugin_id)
        return ActivePlugin(manifest, inst) if inst is not None and manifest is not None else None

    def has(self, kind: str, capability: str) -> bool:
        a = self.active(kind)
        return a is not None and capability in a.manifest.capabilities

    def build_error(self, plugin_id: str) -> str | None:
        return self._build_errors.get(plugin_id)

    def state(self, plugin_id: str) -> dict:
        cfg = self._configs.get(plugin_id)
        return dict(cfg.state) if cfg else {}

    # --- containment -------------------------------------------------------------------------------------------

    async def call(self, kind: str, method: str, *args, timeout: float = DEFAULT_TIMEOUT_S, **kwargs) -> CallResult:
        """Call `method` on the active provider of `kind`. Never raises (except cancellation)."""
        active = self.active(kind)
        if active is None:
            return CallResult(False, error=f"no active {kind} provider", reason="inactive")
        plugin_id, fn = active.manifest.id, getattr(active.instance, method, None)
        if fn is None:
            return CallResult(False, error=f"{plugin_id} has no method {method!r}", reason="inactive")
        try:
            value = await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout)
        except asyncio.TimeoutError:
            return await self._failed(plugin_id, method, f"{method} timed out after {timeout:g}s", "timeout")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return await self._failed(plugin_id, method, f"{method} failed: {e}", "error", exc=e)
        await self._record(plugin_id, last_error=None, last_ok_at=_now())
        return CallResult(True, value=value)

    async def _failed(self, plugin_id: str, method: str, message: str, reason: str, exc: Exception | None = None) -> CallResult:
        logger.warning("Plugin %s: %s", plugin_id, message, exc_info=exc)
        await self._record(plugin_id, last_error=message, last_error_at=_now())
        return CallResult(False, error=message, reason=reason)  # type: ignore[arg-type]

    async def _record(self, plugin_id: str, **patch) -> None:
        """Persist state changes. Only writes when something actually changed (a healthy poll costs no DB write
        beyond the first success after an error)."""
        cfg = self._configs.get(plugin_id)
        if cfg is None or self._session_factory is None:
            return
        healthy_again = patch.get("last_error") is None and cfg.state.get("last_error") is None and "last_ok_at" in patch
        if healthy_again and "last_ok_at" in cfg.state:
            return
        new_state = {**cfg.state, **patch}
        if new_state == cfg.state:
            return
        cfg.state = new_state
        try:
            async with self._session_factory() as s:
                row = await s.get(PluginConfig, plugin_id)
                if row is not None:
                    row.state = new_state
                    await s.commit()
        except Exception:
            logger.exception("Could not persist state for plugin %s", plugin_id)

    # --- configuration (rebuilds in place) ---------------------------------------------------------------------

    async def update_config(self, plugin_id: str, *, enabled: bool | None = None, settings: dict | None = None,
                            secrets: dict | None = None) -> None:
        """Validate + persist a plugin's config, then rebuild. `secrets` omit = keep; empty string clears one."""
        manifest = get_plugin(plugin_id)
        if manifest is None:
            raise PluginError(f"unknown plugin {plugin_id!r}")
        assert self._session_factory is not None
        async with self._session_factory() as s:
            row = await s.get(PluginConfig, plugin_id)
            if row is None:
                row = PluginConfig(plugin_id=plugin_id, enabled=False, settings={}, secrets={}, state={})
                s.add(row)
            new_settings = {**(row.settings or {}), **(settings or {})}
            new_secrets = {**(row.secrets or {})}
            for k, v in (secrets or {}).items():
                if k not in manifest.secret_fields:
                    raise PluginError(f"{k!r} is not a secret field of {plugin_id!r}")
                if v == "":
                    new_secrets.pop(k, None)
                else:
                    new_secrets[k] = v
            manifest.settings_model(**{**new_settings, **new_secrets})        # validate before persisting
            row.settings, row.secrets = new_settings, new_secrets
            if enabled is not None:
                row.enabled = enabled
            row.updated_at = _now()
            await s.commit()
        await self.reload()

    async def set_slot(self, kind: str, plugin_id: str | None) -> None:
        """Choose the provider for `kind` (None = no provider). Selecting a provider enables it (spec §3.1)."""
        if plugin_id is not None:
            manifest = get_plugin(plugin_id)
            if manifest is None or manifest.kind != kind:
                raise PluginError(f"{plugin_id!r} is not a registered {kind} plugin")
        assert self._session_factory is not None
        async with self._session_factory() as s:
            slot = await s.get(ExtensionSlot, kind)
            if slot is None:
                s.add(ExtensionSlot(kind=kind, plugin_id=plugin_id))
            else:
                slot.plugin_id = plugin_id
            if plugin_id is not None:
                row = await s.get(PluginConfig, plugin_id)
                if row is None:
                    s.add(PluginConfig(plugin_id=plugin_id, enabled=True, settings={}, secrets={}, state={}))
                else:
                    row.enabled = True
            await s.commit()
        await self.reload()


plugin_host = PluginHost()
