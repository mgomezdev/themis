"""PluginHost: lifecycle, the active rule, and failure containment (spec §3.1).

* **Active rule (one source of truth):** a plugin serves a capability iff `capability_selections[cap] == id`, it is
  registered and provides `cap`, `plugin_configs[id].enabled`, its `requires` are met and its instance built. Core asks only
  `host.active(cap)` / `host.has(cap, feature)` / `host.part(cap)`.
* **Containment:** every core -> provider call goes through `host.call(...)`: a timeout, every exception caught,
  logged with the plugin id and recorded in `plugin_configs.state.last_error`; the caller gets a typed
  `CallResult`, never an exception. Plugin errors never reach the queue loop or a request handler.
* **No I/O on the queue loop:** the loop never awaits `call()`; it schedules via the host's own tasks / outbox
  (see the deduction model). `active()`/`has()` are synchronous and read an in-memory snapshot.
* A settings change or provider switch rebuilds the instance in place (no restart)."""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Generic, Literal, TypeVar

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import CapabilitySelection, PluginConfig
from ..eventing.definitions import EventSubscription
from . import PluginError, capability_catalog, get_plugin, providers_of, registered_plugins
from . import migrations as plugin_migrations
from .capabilities.definition import BUILDS_ALL, CapabilityDef, RoutedCapability
from .manifest import PluginManifest, Provide

logger = logging.getLogger("app")

DEFAULT_TIMEOUT_S = 10.0
R = TypeVar("R")
P = TypeVar("P")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


@dataclass(frozen=True)
class CallResult(Generic[R]):
    ok: bool
    value: R | None = None
    error: str | None = None
    # ok | inactive (no active provider / method) | timeout | error
    reason: Literal["ok", "inactive", "timeout", "error"] = "ok"
    # The original exception (never persisted; may embed secrets — show it only through `PluginHost.redact`).
    exception: Exception | None = None


@dataclass(frozen=True)
class ActivePlugin:
    manifest: PluginManifest
    instance: Any
    capability: str
    part: Any                                    # the part of the instance that serves `capability`
    features: frozenset[str]


@dataclass(frozen=True)
class CapabilityStatus:
    # serving | waiting (unmet requires) | error (could not build) | disabled | none_selected | no_provider | dormant (definer gone)
    state: Literal["serving", "waiting", "error", "disabled", "none_selected", "no_provider", "dormant"]
    plugin_id: str | None = None
    waiting_on: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class Resolution:
    """Outcome of `PluginHost.resolve`. `plugin_id` is the chosen provider, or None when blocked; `outcome` says why:
    preferred | default | no_provider | preference_dormant | preference_ineligible | default_unavailable | default_ineligible |
    not_choose_one (the capability is not choose-one). `call_for` does not re-check eligibility: use `call_choose`."""
    plugin_id: str | None
    outcome: Literal["preferred", "default", "no_provider", "preference_dormant", "preference_ineligible",
                     "default_unavailable", "default_ineligible", "not_choose_one"]
    requested: str | None = None


def _part_of(instance: Any, provide: Provide) -> Any:
    return instance if provide.attr is None else getattr(instance, provide.attr, None)


@dataclass
class _Snapshot:
    enabled: bool = False
    settings: dict = field(default_factory=dict)
    secrets: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)


class PluginHost:
    def __init__(self) -> None:
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._selections: dict[str, str | None] = {}
        self._explicit: dict[str, bool] = {}
        self._configs: dict[str, _Snapshot] = {}
        self._instances: dict[str, Any] = {}
        self._build_errors: dict[str, str] = {}
        self._rejected: dict[str, dict[str, str]] = {}     # plugin id -> {capability: why that provide entry was refused}
        self._fingerprints: dict[str, tuple] = {}
        # Async callbacks `(plugin_id)` run when a *running* plugin's settings/secrets change (its instance is replaced), so a
        # kind's services can drop anything derived from the old configuration (e.g. a cache of a different server's data).
        self.config_changed_hooks: list = []
        self._lock = asyncio.Lock()          # serialises reload/update_config/set_provider (instance swaps)
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
        """Re-read selections + configs from the DB and bring the active provider instances in line: an instance is only
        rebuilt when its enabled/settings/secrets changed (a change to one plugin never touches another)."""
        async with self._lock:
            await self._reload_locked()

    async def _reload_locked(self) -> None:
        assert self._session_factory is not None, "PluginHost.configure() was not called"
        async with self._session_factory() as s:
            rows = list((await s.execute(select(CapabilitySelection))).scalars())
            selections = {r.capability: r.plugin_id for r in rows}
            explicit = {r.capability: bool(r.explicit) for r in rows}
            for r in rows:                                # an auto-picked provider that is gone must not block auto-select
                if not r.explicit and r.plugin_id and get_plugin(r.plugin_id) is None:
                    await s.delete(r)
                    selections.pop(r.capability, None)
                    explicit.pop(r.capability, None)
            have = {r.plugin_id for r in (await s.execute(select(PluginConfig))).scalars()}
            for m in registered_plugins():                  # a default-enabled provider needs a row so its instance is built
                if m.default_enabled and m.provides and m.id not in have:
                    s.add(PluginConfig(plugin_id=m.id, enabled=True, settings={}, secrets={}, state={}, updated_at=_now()))
            await s.flush()
            configs = {r.plugin_id: _Snapshot(bool(r.enabled), dict(r.settings or {}), dict(r.secrets or {}),
                                              dict(r.state or {}))
                       for r in (await s.execute(select(PluginConfig))).scalars()}
            for cap, d in capability_catalog().items():  # auto-select the unambiguous case only (spec decision 5)
                if cap in selections or d.mode in ("routed", "fan_out"):
                    continue
                enabled = [m.id for m in providers_of(cap) if configs.get(m.id) and configs[m.id].enabled]
                if len(enabled) == 1:
                    s.add(CapabilitySelection(capability=cap, plugin_id=enabled[0], explicit=False))
                    selections[cap], explicit[cap] = enabled[0], False
            await s.commit()
        self._selections, self._explicit, self._configs = selections, explicit, configs
        for _ in range(len(selections) + 1):          # a provider that fails to build takes its dependents down in the same pass
            candidates = {p for p in selections.values() if p} | set(configs)     # routed/fan-out providers are never selected
            wanted = {pid for pid in candidates
                      if self._is_enabled(pid) and self._serves_any(pid) and not self.unmet(pid)}
            for plugin_id in [p for p in self._instances if p not in wanted]:
                await self._close(self._instances.pop(plugin_id), plugin_id)
                self._fingerprints.pop(plugin_id, None)
            self._build_errors = {k: v for k, v in self._build_errors.items() if k in wanted}
            self._rejected = {k: v for k, v in self._rejected.items() if k in wanted}
            errors_before = set(self._build_errors)
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
            if set(self._build_errors) == errors_before:
                break

    def _is_enabled(self, plugin_id: str) -> bool:
        cfg = self._configs.get(plugin_id)
        if cfg is None:                                  # never configured: the manifest's default applies
            m = get_plugin(plugin_id)
            return bool(m and m.default_enabled)
        return bool(cfg.enabled and get_plugin(plugin_id) is not None)

    def _serves_any(self, plugin_id: str) -> bool:
        m, catalog = get_plugin(plugin_id), capability_catalog()
        if m is not None and (m.subscribes or m.defines_events):     # event subscribers/publishers are built even when they serve no selected capability
            return True
        return m is not None and any(cap in catalog and (catalog[cap].mode in BUILDS_ALL or self._selections.get(cap) == plugin_id)
                                     for cap in m.provides)

    def unmet(self, plugin_id: str, _seen: frozenset[str] = frozenset()) -> tuple[str, ...]:
        """Capability ids this plugin `requires` that nobody enabled (and itself satisfied) currently serves at a high enough
        version. A requirement cycle never recurses forever: a plugin already on the walk counts as unmet."""
        m = get_plugin(plugin_id)
        if m is None:
            return ()
        catalog, out = capability_catalog(), []
        for req in m.requires:
            pid = self._selections.get(req.capability)
            provider = get_plugin(pid) if pid else None
            prov = provider.provides.get(req.capability) if provider else None
            ok = (req.capability in catalog and pid is not None and pid != plugin_id and pid not in _seen
                  and self._is_enabled(pid) and pid not in self._build_errors and prov is not None
                  and prov.version >= req.min_version and not self.unmet(pid, _seen | {plugin_id}))
            if not ok:
                out.append(req.capability)
        return tuple(out)

    @staticmethod
    def _check_contract(manifest: PluginManifest, instance: Any) -> dict[str, str]:
        """Duck-typing check per provide entry: the named part exists, the contract version matches the definition and the
        definition's required async methods are there. Returns {capability: reason} for the entries that fail; the others keep
        working (a plugin serving several capabilities is not taken down by one bad entry)."""
        catalog, rejected = capability_catalog(), {}
        for cap, prov in manifest.provides.items():
            d = catalog.get(cap)
            part = _part_of(instance, prov)
            if part is None:
                rejected[cap] = f"provides {cap} through attribute {prov.attr!r}, which the instance does not have"
            elif d is None:
                continue                                    # definer not registered: nothing to verify yet
            elif d.version != prov.version:
                rejected[cap] = f"provides {cap} v{prov.version} but this Themis knows v{d.version}"
            elif d.protocol is not None and not isinstance(part, d.protocol):
                rejected[cap] = f"{cap}: does not satisfy {d.protocol.__name__}"
            else:
                missing = [n for n in d.required_methods if not inspect.iscoroutinefunction(getattr(part, n, None))]
                if missing:
                    rejected[cap] = f"{cap}: missing async method(s) {', '.join(missing)}"
        return rejected

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
            rejected = self._check_contract(manifest, built)
        except Exception as e:                          # contained: a bad config must not stop Themis
            message = self._redact(plugin_id, f"could not build provider: {self._describe(e)}")
            logger.warning("Plugin %s could not be built: %s", plugin_id, message)
            self._instances.pop(plugin_id, None)
            self._rejected.pop(plugin_id, None)
            self._build_errors[plugin_id] = message
            await self._record(plugin_id, last_error=message, last_error_at=_now())
        else:
            self._rejected[plugin_id] = rejected
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

    def _mode(self, cap: str) -> str | None:
        d = capability_catalog().get(cap)
        return d.mode if d else None

    def _is_routed(self, cap: str) -> bool:
        return self._mode(cap) == "routed"

    def _builds_all(self, cap: str) -> bool:
        return self._mode(cap) in BUILDS_ALL

    def _unselectable(self, cap: str) -> bool:
        return self._mode(cap) in ("routed", "fan_out")

    def active(self, cap: str) -> ActivePlugin | None:
        if self._unselectable(cap):
            return None                                       # routed/fan-out: one provider per resource (or all), not one overall
        pid = self._selections.get(cap)
        m = get_plugin(pid) if pid else None
        prov = m.provides.get(cap) if m else None
        inst = self._instances.get(pid) if pid else None
        if m is None or prov is None or inst is None or cap not in capability_catalog() or not self._is_enabled(pid):
            return None
        if cap in self._rejected.get(pid, {}):
            return None
        return ActivePlugin(m, inst, cap, _part_of(inst, prov), prov.features)

    def active_for(self, cap: str, plugin_id: str) -> ActivePlugin | None:
        """The multi-provider capability `cap` as served by `plugin_id`, or None when that plugin is not an active provider of it."""
        if not self._builds_all(cap) or not self._is_enabled(plugin_id):
            return None
        m, inst = get_plugin(plugin_id), self._instances.get(plugin_id)
        prov = m.provides.get(cap) if m else None
        if m is None or prov is None or inst is None or cap in self._rejected.get(plugin_id, {}):
            return None
        return ActivePlugin(m, inst, cap, _part_of(inst, prov), prov.features)

    def active_providers(self, cap: str) -> list[str]:
        """Ids of the enabled, built providers of a routed/choose-one/fan-out capability (what a resource may be bound to)."""
        return [m.id for m in providers_of(cap) if self.active_for(cap, m.id) is not None]

    def has(self, cap: str, feature: str) -> bool:
        a = self.active(cap)
        return a is not None and feature in a.features

    def part(self, cap: str) -> Any | None:
        a = self.active(cap)
        return a.part if a else None

    def selected(self, cap: str) -> str | None:
        """The stored choice for `cap` (it may not be active: disabled, waiting, or built with an error)."""
        return self._selections.get(cap)

    def is_explicit(self, cap: str) -> bool:
        return self._explicit.get(cap, False)

    def selections(self) -> dict[str, str | None]:
        return dict(self._selections)

    def status(self, cap: str) -> CapabilityStatus:
        if cap not in capability_catalog():
            return CapabilityStatus("dormant", self._selections.get(cap))
        if self._unselectable(cap):
            if not providers_of(cap):
                return CapabilityStatus("no_provider")
            return CapabilityStatus("serving") if self.active_providers(cap) else CapabilityStatus("disabled")
        pid = self._selections.get(cap)
        if pid is None:
            return CapabilityStatus("none_selected" if cap in self._selections or providers_of(cap) else "no_provider")
        m = get_plugin(pid)
        if m is None or cap not in m.provides:
            return CapabilityStatus("no_provider", pid)
        if not self._is_enabled(pid):
            return CapabilityStatus("disabled", pid)
        missing = self.unmet(pid)
        if missing:
            return CapabilityStatus("waiting", pid, waiting_on=missing)
        if self._build_errors.get(pid):
            return CapabilityStatus("error", pid, error=self._build_errors[pid])
        if cap in self._rejected.get(pid, {}):
            return CapabilityStatus("error", pid, error=self._rejected[pid][cap])
        return CapabilityStatus("serving", pid) if self.active(cap) else CapabilityStatus("error", pid)

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
        return self._is_enabled(plugin_id)

    def has_secret(self, plugin_id: str, field: str) -> bool:
        cfg = self._configs.get(plugin_id)
        return bool(cfg and cfg.secrets.get(field))

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
        self._selections, self._explicit, self._configs, self._instances = {}, {}, {}, {}
        self._build_errors, self._fingerprints, self._rejected = {}, {}, {}
        # Fresh locks: a lock is bound to the event loop that first contended it, and a task killed mid-hold when a
        # test's loop closes would leave it locked forever for the next test.
        self._lock, self._state_lock = asyncio.Lock(), asyncio.Lock()

    # --- choose-one and fan-out dispatch -------------------------------------------------------------------------

    def resolve(self, cap: str, *, preferred: str | None = None,
                eligible: Callable[[str], bool] = lambda plugin_id: True) -> Resolution:
        """Pick the provider of a choose-one capability for one operation: the resource's `preferred` provider, else the
        capability default (the selection row), else blocked. Eligibility is checked before dispatch, and an explicit
        preference that is dormant (disabled/removed) or ineligible BLOCKS rather than silently falling back to another
        implementation. Pure and synchronous: nothing is called."""
        if self._mode(cap) != "choose_one":
            return Resolution(None, "not_choose_one")
        if preferred is not None:
            if self.active_for(cap, preferred) is None:
                return Resolution(None, "preference_dormant", preferred)
            if not eligible(preferred):
                return Resolution(None, "preference_ineligible", preferred)
            return Resolution(preferred, "preferred", preferred)
        default = self._selections.get(cap)
        if default is None:
            return Resolution(None, "no_provider")
        if self.active_for(cap, default) is None:
            return Resolution(None, "default_unavailable", default)
        if not eligible(default):
            return Resolution(None, "default_ineligible", default)
        return Resolution(default, "default", default)

    def eligible_providers(self, cap: str, eligible: Callable[[str], bool] = lambda plugin_id: True) -> list[str]:
        """What a selection UI may offer: the enabled, built providers that pass `eligible`."""
        return [pid for pid in self.active_providers(cap) if eligible(pid)]

    async def call_choose(self, cap: RoutedCapability[P] | str, fn: Callable[[P], R], *, preferred: str | None = None,
                          eligible: Callable[[str], bool] = lambda plugin_id: True,
                          timeout: float = DEFAULT_TIMEOUT_S) -> CallResult[R]:
        """`resolve` then `call_for`. A blocked resolution is `reason="inactive"` with the outcome in `error`."""
        cap_id = cap.id if isinstance(cap, RoutedCapability) else cap
        r = self.resolve(cap_id, preferred=preferred, eligible=eligible)
        if r.plugin_id is None:
            return CallResult(False, error=f"{cap_id}: {r.outcome}" + (f" ({r.requested})" if r.requested else ""), reason="inactive")
        return await self.call_for(cap_id, r.plugin_id, fn, timeout=timeout)

    async def fan_out(self, cap: RoutedCapability[P] | str, fn: Callable[[P], R], *,
                      timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, CallResult[R]]:
        """Deliver to every enabled provider of a fan-out capability concurrently. Each call is contained on its own, so a
        provider that fails or times out never blocks or fails the others."""
        cap_id = cap.id if isinstance(cap, RoutedCapability) else cap
        if self._mode(cap_id) != "fan_out":
            return {}
        ids = self.active_providers(cap_id)
        results = await asyncio.gather(*(self.call_for(cap_id, pid, fn, timeout=timeout) for pid in ids))
        return dict(zip(ids, results))

    # --- events (BIZ-249) ----------------------------------------------------------------------------------------

    def event_subscriptions(self, event: str) -> list[tuple[str, EventSubscription]]:
        """(plugin id, subscription) for every enabled, built plugin subscribed to `event` whose handler is an async method.
        Resolved at delivery time, so disabling or removing a plugin stops its deliveries without any unregistering."""
        out = []
        for m in sorted(registered_plugins(), key=lambda m: m.id):
            inst = self._instances.get(m.id)
            if inst is None or not self._is_enabled(m.id):
                continue
            for sub in m.subscribes:
                if sub.event == event and inspect.iscoroutinefunction(getattr(inst, sub.handler, None)):
                    out.append((m.id, sub))
        return out

    def broken_subscriptions(self) -> list[dict]:
        """Subscriptions of running plugins whose handler is missing or not `async` (reported to operators, never delivered to)."""
        out = []
        for m in registered_plugins():
            inst = self._instances.get(m.id)
            if inst is None or not self._is_enabled(m.id):
                continue
            out += [{"plugin_id": m.id, "event": s.event, "handler": s.handler} for s in m.subscribes
                    if not inspect.iscoroutinefunction(getattr(inst, s.handler, None))]
        return out

    async def deliver_event(self, plugin_id: str, handler: str, envelope: Any, *, timeout: float) -> CallResult:
        """Run one plugin's event handler with the same containment as any provider call (timeout, exceptions caught, redacted,
        recorded in the plugin's state). Never raises (except cancellation)."""
        inst = self._instances.get(plugin_id)
        fn = getattr(inst, handler, None) if inst is not None and self._is_enabled(plugin_id) else None
        if fn is None:
            return CallResult(False, error=f"{plugin_id} is not running or has no handler {handler!r}", reason="inactive")
        return await self._contained(plugin_id, inst, f"event handler {handler}", lambda: fn(envelope), timeout)

    # --- containment -------------------------------------------------------------------------------------------

    async def call(self, cap: str, method: str, *args, timeout: float = DEFAULT_TIMEOUT_S, **kwargs) -> CallResult:
        """Call `method` on the part of the active provider of `cap` that serves it. Never raises (except cancellation)."""
        active = self.active(cap)
        if active is None:
            return CallResult(False, error=f"no active {cap} provider", reason="inactive")
        fn = getattr(active.part, method, None)
        if fn is None:
            return CallResult(False, error=f"{active.manifest.id} has no method {method!r}", reason="inactive")
        return await self._contained(active.manifest.id, active.instance, method, lambda: fn(*args, **kwargs), timeout)

    async def call_for(self, cap: RoutedCapability[P] | str, plugin_id: str, fn: Callable[[P], R], *,
                       timeout: float = DEFAULT_TIMEOUT_S) -> CallResult[R]:
        """Run `fn` against the routed capability `cap` as served by `plugin_id` (the provider bound to the resource).

        `fn` takes the provider and returns its result; a blocking method runs in a worker thread so the event loop is never
        held, an async one is awaited. Typed: `RoutedCapability[P]` makes `p` a `P` in the lambda. Never raises (except
        cancellation); an unbound or inactive provider is `reason="inactive"`. A blocking method that outlives `timeout` is abandoned,
        not stopped: `asyncio.wait_for` cannot interrupt a worker thread, which keeps running until the call returns."""
        cap_id = cap.id if isinstance(cap, RoutedCapability) else cap
        active = self.active_for(cap_id, plugin_id)
        if active is None:
            return CallResult(False, error=f"no active {cap_id} provider {plugin_id!r}", reason="inactive")

        async def run() -> R:
            out = await asyncio.to_thread(fn, active.part)
            return await out if inspect.isawaitable(out) else out

        return await self._contained(plugin_id, active.instance, cap_id, run, timeout)

    async def _contained(self, plugin_id: str, instance: Any, what: str, run: Callable[[], Awaitable[Any]],
                         timeout: float) -> CallResult:
        try:
            value = await asyncio.wait_for(run(), timeout=timeout)
        except asyncio.TimeoutError:
            return await self._failed(plugin_id, instance, f"{what} timed out after {timeout:g}s", "timeout")
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():          # *we* were cancelled: propagate
                raise
            return await self._failed(plugin_id, instance, f"{what} was cancelled inside the plugin", "error")
        except Exception as e:
            return await self._failed(plugin_id, instance, f"{what} failed: {self._describe(e)}", "error", exc=e)
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
                    row = PluginConfig(plugin_id=plugin_id, enabled=manifest.default_enabled, settings={}, secrets={}, state={})
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

    async def set_provider(self, cap: str, plugin_id: str | None) -> None:
        """Choose the provider of `cap` (None = none, remembered). Selecting a provider enables it (spec §3)."""
        if cap not in capability_catalog():
            if plugin_id is None and cap in self._selections:        # a dormant choice (its definer is gone): clearing forgets it
                await self._forget_selection(cap)
                return
            raise PluginError(f"unknown capability {cap!r}")
        if self._unselectable(cap):
            raise PluginError(f"{cap} is {self._mode(cap).replace('_', '-')}: every enabled provider serves it; enable the plugin instead")
        if plugin_id is not None:
            manifest = get_plugin(plugin_id)
            if manifest is None or cap not in manifest.provides:
                raise PluginError(f"{plugin_id!r} does not provide {cap}")
            self._reject_cycle(cap, plugin_id)
        assert self._session_factory is not None
        async with self._lock:
            async with self._session_factory() as s:
                row = await s.get(CapabilitySelection, cap)
                if row is None:
                    s.add(CapabilitySelection(capability=cap, plugin_id=plugin_id, explicit=True))
                else:
                    row.plugin_id, row.explicit = plugin_id, True
                if plugin_id is not None:
                    cfg = await s.get(PluginConfig, plugin_id)
                    if cfg is None:
                        s.add(PluginConfig(plugin_id=plugin_id, enabled=True, settings={}, secrets={}, state={}))
                    else:
                        cfg.enabled = True
                await s.commit()
            await self._reload_locked()

    async def _forget_selection(self, cap: str) -> None:
        assert self._session_factory is not None
        async with self._lock:
            async with self._session_factory() as s:
                row = await s.get(CapabilitySelection, cap)
                if row is not None:
                    await s.delete(row)
                    await s.commit()
            await self._reload_locked()

    def _reject_cycle(self, cap: str, plugin_id: str) -> None:
        """Selecting `plugin_id` for `cap` must not make it (transitively) require itself through the selected providers."""
        sel = {**self._selections, cap: plugin_id}
        stack: list[str] = []

        def walk(pid: str, seen: set[str]) -> bool:
            stack.append(pid)
            m = get_plugin(pid)
            for req in (m.requires if m else ()):
                nxt = sel.get(req.capability)
                if nxt is None:
                    continue
                if nxt == plugin_id:
                    stack.append(plugin_id)
                    return True
                if nxt not in seen and walk(nxt, seen | {nxt}):
                    return True
            stack.pop()
            return False

        if walk(plugin_id, {plugin_id}):
            raise PluginError(f"selecting {plugin_id!r} for {cap} would create a requirement cycle ({' -> '.join(stack)})")


plugin_host = PluginHost()
