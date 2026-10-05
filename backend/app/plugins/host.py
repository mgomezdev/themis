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

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import ExtensionSlot, PluginConfig
from . import PluginError, get_plugin
from . import migrations as plugin_migrations
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
    # The original exception (never persisted; may embed secrets — show it only through `PluginHost.redact`).
    exception: Exception | None = None


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
        self._fingerprints: dict[str, tuple] = {}
        # Async callbacks `(plugin_id)` run when a *running* plugin's settings/secrets change (its instance is replaced), so a
        # kind's services can drop anything derived from the old configuration (e.g. a cache of a different server's data).
        self.config_changed_hooks: list = []
        self._lock = asyncio.Lock()          # serialises reload/update_config/set_slot (instance swaps)
        self._state_lock = asyncio.Lock()    # serialises state persistence (memory + DB stay in step)

    # --- lifecycle -------------------------------------------------------------------------------------------

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession] | None:
        return self._session_factory

    def configure(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def start(self) -> None:
        await self.reload()

    async def stop(self) -> None:
        async with self._lock:
            for plugin_id in list(self._instances):
                await self._close(self._instances.pop(plugin_id), plugin_id)
            self._fingerprints.clear()

    async def reload(self) -> None:
        """Re-read slots + configs from the DB and bring the active provider instances in line: an instance is only
        rebuilt when its enabled/settings/secrets changed (a change to one plugin never touches another)."""
        async with self._lock:
            await self._reload_locked()

    async def _reload_locked(self) -> None:
        assert self._session_factory is not None, "PluginHost.configure() was not called"
        async with self._session_factory() as s:
            slots = {r.kind: r.plugin_id for r in (await s.execute(select(ExtensionSlot))).scalars()}
            configs = {r.plugin_id: _Snapshot(bool(r.enabled), dict(r.settings or {}), dict(r.secrets or {}),
                                              dict(r.state or {}))
                       for r in (await s.execute(select(PluginConfig))).scalars()}
        self._slots, self._configs = slots, configs
        wanted = {pid for pid in slots.values() if pid and self._is_enabled(pid)}
        for plugin_id in [p for p in self._instances if p not in wanted]:
            await self._close(self._instances.pop(plugin_id), plugin_id)
            self._fingerprints.pop(plugin_id, None)
        self._build_errors = {k: v for k, v in self._build_errors.items() if k in wanted}
        for plugin_id in sorted(wanted):
            cfg = configs[plugin_id]
            fingerprint = (repr(sorted(cfg.settings.items())), repr(sorted(cfg.secrets.items())),
                           plugin_migrations.failed.get(plugin_id))
            if self._fingerprints.get(plugin_id) == fingerprint and (plugin_id in self._instances or plugin_id in self._build_errors):
                continue
            changed = plugin_id in self._fingerprints                    # a known instance whose configuration changed
            await self._rebuild(plugin_id)
            self._fingerprints[plugin_id] = fingerprint
            if changed:
                for hook in self.config_changed_hooks:
                    try:
                        await hook(plugin_id)
                    except Exception:
                        logger.warning("Config-changed hook failed for plugin %s", plugin_id)

    def _is_enabled(self, plugin_id: str) -> bool:
        cfg = self._configs.get(plugin_id)
        return bool(cfg and cfg.enabled and get_plugin(plugin_id) is not None)

    async def _close(self, instance: Any, plugin_id: str) -> None:
        closer = getattr(instance, "aclose", None)
        if closer is not None:
            try:
                await closer()
            except Exception:
                logger.warning("Plugin %s failed to close", plugin_id)

    def _secrets_of(self, plugin_id: str) -> list[str]:
        cfg = self._configs.get(plugin_id)
        return [str(v) for v in (cfg.secrets.values() if cfg else []) if v]

    def _redact(self, plugin_id: str, text: str, extra: tuple[str, ...] = ()) -> str:
        """Never let a secret reach persisted state, logs or an API message (provider/validation errors often embed it)."""
        for secret in sorted([*self._secrets_of(plugin_id), *extra], key=len, reverse=True):
            text = text.replace(secret, "***")
        return text

    async def _rebuild(self, plugin_id: str) -> None:
        manifest, cfg = get_plugin(plugin_id), self._configs[plugin_id]
        previous = self._instances.get(plugin_id)
        migration_error = plugin_migrations.failed.get(plugin_id)
        try:
            if migration_error:
                raise PluginError(migration_error)
            settings = manifest.settings_model(**{**cfg.settings, **cfg.secrets})
            built = manifest.factory(settings)
        except Exception as e:                          # contained: a bad config must not stop Themis
            message = self._redact(plugin_id, f"could not build provider: {self._describe(e)}")
            logger.warning("Plugin %s could not be built: %s", plugin_id, message)
            self._instances.pop(plugin_id, None)
            self._build_errors[plugin_id] = message
            await self._record(plugin_id, last_error=message, last_error_at=_now())
        else:
            self._instances[plugin_id] = built          # swap first, then close the old one: no window where a closed instance is active
            self._build_errors.pop(plugin_id, None)
            await self._record(plugin_id, last_error=None)
        if previous is not None:
            await self._close(previous, plugin_id)

    @staticmethod
    def _describe(e: Exception) -> str:
        """A ValidationError's str() embeds `input_value=<the offending value>`; name the fields instead."""
        if isinstance(e, ValidationError):
            return "invalid settings: " + "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        return str(e)

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

    def settings(self, plugin_id: str) -> dict:
        """The plugin's stored non-secret settings (no model defaults applied)."""
        cfg = self._configs.get(plugin_id)
        return dict(cfg.settings) if cfg else {}

    def is_enabled(self, plugin_id: str) -> bool:
        cfg = self._configs.get(plugin_id)
        return bool(cfg and cfg.enabled)

    def has_secret(self, plugin_id: str, field: str) -> bool:
        cfg = self._configs.get(plugin_id)
        return bool(cfg and cfg.secrets.get(field))

    def slot(self, kind: str) -> str | None:
        return self._slots.get(kind)

    def redact(self, plugin_id: str, text: str, extra: tuple[str, ...] = ()) -> str:
        """`text` with the plugin's secret values (and any `extra` candidate secrets the caller is trying) masked — for
        messages built from a CallResult.exception or a failed connection test."""
        return self._redact(plugin_id, text, tuple(x for x in extra if x))

    def build_candidate(self, plugin_id: str, settings: dict | None = None, secrets: dict | None = None) -> Any:
        """A throw-away provider instance for `plugin_id` built from the saved config overlaid with the given values
        (nothing is persisted; saved secrets never leave the host). Used to "test connection" before saving. Raises
        `PluginError` for an unknown plugin / invalid settings, and whatever the factory raises."""
        manifest = get_plugin(plugin_id)
        if manifest is None:
            raise PluginError(f"unknown plugin {plugin_id!r}")
        cfg = self._configs.get(plugin_id) or _Snapshot()
        merged_secrets = {**cfg.secrets}
        for k, v in (secrets or {}).items():
            if k not in manifest.secret_fields:
                raise PluginError(f"{k!r} is not a secret field of {plugin_id!r}")
            if v == "":
                merged_secrets.pop(k, None)
            else:
                merged_secrets[k] = v
        leaked = sorted(set(settings or {}) & manifest.secret_fields)
        if leaked:
            raise PluginError(f"{leaked} are secret fields: send them as secrets, not settings")
        try:
            model = manifest.settings_model(**{**cfg.settings, **(settings or {}), **merged_secrets})
        except ValidationError as e:
            raise PluginError(self._describe(e)) from None
        return manifest.factory(model)

    async def record_state(self, plugin_id: str, **patch) -> None:
        """Merge keys into the plugin's persisted state (sync health, connection status...)."""
        await self._record(plugin_id, **patch)

    def _reset(self) -> None:
        """Forget everything in memory (tests; the DB is untouched)."""
        self._slots, self._configs, self._instances = {}, {}, {}
        self._build_errors, self._fingerprints = {}, {}
        # Fresh locks: a lock is bound to the event loop that first contended it, and a task killed mid-hold when a
        # test's loop closes would leave it locked forever for the next test.
        self._lock, self._state_lock = asyncio.Lock(), asyncio.Lock()

    # --- containment -------------------------------------------------------------------------------------------

    async def call(self, kind: str, method: str, *args, timeout: float = DEFAULT_TIMEOUT_S, **kwargs) -> CallResult:
        """Call `method` on the active provider of `kind`. Never raises (except cancellation)."""
        active = self.active(kind)
        if active is None:
            return CallResult(False, error=f"no active {kind} provider", reason="inactive")
        plugin_id, instance = active.manifest.id, active.instance
        fn = getattr(instance, method, None)
        if fn is None:
            return CallResult(False, error=f"{plugin_id} has no method {method!r}", reason="inactive")
        try:
            value = await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout)
        except asyncio.TimeoutError:
            return await self._failed(plugin_id, instance, f"{method} timed out after {timeout:g}s", "timeout")
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():          # *we* were cancelled: propagate
                raise
            return await self._failed(plugin_id, instance, f"{method} was cancelled inside the plugin", "error")
        except Exception as e:
            return await self._failed(plugin_id, instance, f"{method} failed: {self._describe(e)}", "error", exc=e)
        if self._instances.get(plugin_id) is instance:
            await self._record(plugin_id, last_error=None, last_ok_at=_now())
        return CallResult(True, value=value)

    async def _failed(self, plugin_id: str, instance: Any, message: str, reason: str, exc: Exception | None = None) -> CallResult:
        message = self._redact(plugin_id, message)
        logger.warning("Plugin %s: %s", plugin_id, message)
        if self._instances.get(plugin_id) is instance:          # a call that straddled a reload must not blame the new instance
            await self._record(plugin_id, last_error=message, last_error_at=_now())
        return CallResult(False, error=message, reason=reason, exception=exc)  # type: ignore[arg-type]

    async def _record(self, plugin_id: str, **patch) -> None:
        """Persist state changes (DB first, then memory, under a lock so they cannot drift). Only writes when
        something changed: a healthy poll costs no DB write beyond the first success after an error."""
        async with self._state_lock:
            cfg = self._configs.get(plugin_id)
            if cfg is None or self._session_factory is None:
                return
            if patch.get("last_error") is None and cfg.state.get("last_error") is None and "last_ok_at" in patch \
                    and "last_ok_at" in cfg.state:
                return
            new_state = {**cfg.state, **patch}
            if new_state == cfg.state:
                return
            try:
                async with self._session_factory() as s:
                    row = await s.get(PluginConfig, plugin_id)
                    if row is not None:
                        row.state = new_state
                        await s.commit()
            except Exception:
                logger.warning("Could not persist state for plugin %s", plugin_id)
                return                                                   # memory unchanged: the next change retries
            cfg.state = new_state

    # --- configuration (rebuilds in place) ---------------------------------------------------------------------

    async def update_config(self, plugin_id: str, *, enabled: bool | None = None, settings: dict | None = None,
                            secrets: dict | None = None) -> None:
        """Validate + persist a plugin's config, then rebuild. `secrets` omit = keep; empty string clears one.
        Raises `PluginError` (never echoing a secret) for an unknown plugin, a bad field or invalid settings."""
        manifest = get_plugin(plugin_id)
        if manifest is None:
            raise PluginError(f"unknown plugin {plugin_id!r}")
        assert self._session_factory is not None
        leaked = sorted(set(settings or {}) & manifest.secret_fields)
        if leaked:
            raise PluginError(f"{leaked} are secret fields: send them as secrets, not settings")
        async with self._lock:
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
                try:
                    manifest.settings_model(**{**new_settings, **new_secrets})        # validate before persisting
                except ValidationError as e:
                    raise PluginError(self._describe(e)) from None
                row.settings, row.secrets = new_settings, new_secrets
                if enabled is not None:
                    row.enabled = enabled
                row.updated_at = _now()
                await s.commit()
            await self._reload_locked()

    async def set_slot(self, kind: str, plugin_id: str | None) -> None:
        """Choose the provider for `kind` (None = no provider). Selecting a provider enables it (spec §3.1)."""
        if plugin_id is not None:
            manifest = get_plugin(plugin_id)
            if manifest is None or manifest.kind != kind:
                raise PluginError(f"{plugin_id!r} is not a registered {kind} plugin")
        assert self._session_factory is not None
        async with self._lock:
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
            await self._reload_locked()


plugin_host = PluginHost()
