"""Model -> slicer machine profile mapping with a per-printer nozzle variant (BIZ-294).

* `printer_model_slicer_profiles(model_uuid, provider_plugin_id, nozzle_diameter_mm, flow, machine_profile, is_default)`: the profile is
  set once per model (per slicing provider), exactly one default row per (model, provider).
* `printers.nozzle_variant` (JSON, NULL = the model's default variant).
* `printers.current_orca_printer_profile` is retired as the make/model key and renamed `machine_profile_override`: the raw per-printer
  escape hatch for custom presets and printers with no registered model.
* `job_model_targets.machine_profile` (a make/model string) becomes `model_uuid`.

Alpha: migrate, do not preserve. Backfill: per (model, provider) the printers' distinct current profiles become mapping rows, the most
common one the default. A differing printer gets a variant row (diameter / high-flow parsed from the profile name) when that is
unambiguous, else keeps its profile as the raw override; printers with no model keep the override. A model target is re-keyed to the
model of the printers that were on its profile; one with no such printer is dropped (nothing could ever match it)."""
from __future__ import annotations

import json
import re
from collections import Counter

from sqlalchemy import text

version = 52
name = "model_slicer_profiles"
reshapes_schema = True   # renames a column and rebuilds a table (tests/test_migrations.py skips it when it looks for a remove-only rollback)

PROVIDER = "laminus"   # the only slicing provider before this migration; its profile names are what the printers held
_NOZZLE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mm\b)?[^\d]*?nozzle", re.IGNORECASE)   # "0.4 nozzle", "0.4 high flow nozzle"
_HIGH_FLOW = re.compile(r"high[\s_-]*flow|\bHF\b", re.IGNORECASE)

_TARGETS_DDL = """
    CREATE TABLE job_model_targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        {key_col},
        print_profile VARCHAR(512) NOT NULL,
        filament_profile VARCHAR(512),
        filament_id INTEGER,
        material_provider VARCHAR(64),
        material_ref VARCHAR(128),
        filament_type VARCHAR(100) NOT NULL DEFAULT 'any',
        filament_color VARCHAR(20) NOT NULL DEFAULT 'any',
        filament_map JSON
    )"""
_TARGET_COLS = ("job_id, print_profile, filament_profile, filament_id, material_provider, material_ref, filament_type, "
                "filament_color, filament_map")


async def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


def parse_variant(profile: str) -> tuple[float, str] | None:
    """(diameter_mm, flow) read from a machine profile NAME ("... 0.6 nozzle", "... 0.4 high flow nozzle"), or None."""
    m = _NOZZLE.search(profile or "")
    if not m:
        return None
    return float(m.group(1)), ("high_flow" if _HIGH_FLOW.search(profile) else "standard")


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS printer_model_slicer_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_uuid VARCHAR(36) NOT NULL REFERENCES printer_models(id),
            provider_plugin_id VARCHAR(64) NOT NULL,
            nozzle_diameter_mm FLOAT NOT NULL,
            flow VARCHAR(16) NOT NULL DEFAULT 'standard',
            machine_profile VARCHAR(255) NOT NULL,
            is_default BOOLEAN NOT NULL DEFAULT 0,
            UNIQUE (model_uuid, provider_plugin_id, nozzle_diameter_mm, flow)
        )"""))
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_printer_model_slicer_profiles_model_uuid ON printer_model_slicer_profiles (model_uuid)"))
    await conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_printer_model_slicer_profiles_default "
        "ON printer_model_slicer_profiles (model_uuid, provider_plugin_id) WHERE is_default = 1"))

    cols = await _columns(conn, "printers")
    first_run = "current_orca_printer_profile" in cols       # re-running on a migrated DB must not backfill (or rebuild) twice
    if "nozzle_variant" not in cols:
        await conn.execute(text("ALTER TABLE printers ADD COLUMN nozzle_variant JSON"))
    if "current_orca_printer_profile" in cols:
        await conn.execute(text("ALTER TABLE printers RENAME COLUMN current_orca_printer_profile TO machine_profile_override"))

    if not first_run:
        return
    printers = (await conn.execute(text(
        "SELECT id, model_uuid, machine_profile_override, preferred_slicer_plugin_id FROM printers"))).fetchall()
    model_of_profile: dict[str, Counter] = {}
    groups: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for pid, model_uuid, profile, preferred in printers:
        if not model_uuid or not profile:
            continue
        model_of_profile.setdefault(profile, Counter())[model_uuid] += 1
        groups.setdefault((model_uuid, preferred or PROVIDER), []).append((pid, profile))

    for (model_uuid, provider), members in groups.items():
        counts = Counter(p for _, p in members)
        default = sorted(counts, key=lambda p: (-counts[p], p))[0]
        d_diam, d_flow = parse_variant(default) or (0.4, "standard")
        await conn.execute(text(
            "INSERT INTO printer_model_slicer_profiles (model_uuid, provider_plugin_id, nozzle_diameter_mm, flow, machine_profile,"
            " is_default) VALUES (:m, :p, :d, :f, :mp, 1)"),
            {"m": model_uuid, "p": provider, "d": d_diam, "f": d_flow, "mp": default})
        taken = {(d_diam, d_flow): default}
        for pid, profile in members:
            if profile == default:
                await conn.execute(text("UPDATE printers SET machine_profile_override = NULL WHERE id = :i"), {"i": pid})
                continue
            variant = parse_variant(profile)
            if variant is None or taken.get(variant, profile) != profile:
                continue          # ambiguous: the printer keeps its profile as the raw override
            if variant not in taken:
                taken[variant] = profile
                await conn.execute(text(
                    "INSERT INTO printer_model_slicer_profiles (model_uuid, provider_plugin_id, nozzle_diameter_mm, flow,"
                    " machine_profile, is_default) VALUES (:m, :p, :d, :f, :mp, 0)"),
                    {"m": model_uuid, "p": provider, "d": variant[0], "f": variant[1], "mp": profile})
            await conn.execute(text("UPDATE printers SET nozzle_variant = :v, machine_profile_override = NULL WHERE id = :i"),
                               {"v": json.dumps({"diameter_mm": variant[0], "flow": variant[1]}), "i": pid})

    # job_model_targets: machine_profile -> model_uuid (table rebuilt: SQLite cannot add a NOT NULL foreign key in place).
    if "machine_profile" not in await _columns(conn, "job_model_targets"):
        return                                    # created from the current models by v001: already keyed by model_uuid
    await conn.execute(text("DROP INDEX IF EXISTS ix_job_model_targets_machine_profile"))
    await conn.execute(text("ALTER TABLE job_model_targets RENAME TO job_model_targets_old"))
    await conn.execute(text(_TARGETS_DDL.format(key_col="model_uuid VARCHAR(36) NOT NULL REFERENCES printer_models(id)")))
    old = (await conn.execute(text(f"SELECT id, machine_profile, {_TARGET_COLS} FROM job_model_targets_old"))).mappings().all()
    for row in old:
        votes = model_of_profile.get(row["machine_profile"])
        if not votes:
            continue
        await conn.execute(text(
            f"INSERT INTO job_model_targets (id, model_uuid, {_TARGET_COLS}) "
            "VALUES (:id, :mu, :job_id, :print_profile, :filament_profile, :filament_id, :material_provider, :material_ref,"
            " :filament_type, :filament_color, :filament_map)"),
            {"id": row["id"], "mu": votes.most_common(1)[0][0],
             **{k: row[k] for k in ("job_id", "print_profile", "filament_profile", "filament_id", "material_provider",
                                    "material_ref", "filament_type", "filament_color", "filament_map")}})
    await conn.execute(text("DROP TABLE job_model_targets_old"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_model_targets_job_id ON job_model_targets (job_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_model_targets_model_uuid ON job_model_targets (model_uuid)"))


async def _profile_for(conn, model_uuid: str, provider: str, variant: dict | None) -> str | None:
    rows = (await conn.execute(text(
        "SELECT nozzle_diameter_mm, flow, machine_profile, is_default FROM printer_model_slicer_profiles"
        " WHERE model_uuid = :m AND provider_plugin_id = :p"), {"m": model_uuid, "p": provider})).fetchall()
    if variant:
        for d, f, mp, _ in rows:
            if d == variant.get("diameter_mm") and f == variant.get("flow", "standard"):
                return mp
    return next((mp for _, _, mp, is_def in rows if is_def), None)


async def down(conn) -> None:
    """Best effort back to the make/model-profile world: each printer without an override gets the profile its mapping resolves to,
    and a target gets its model's default profile (dropped when the model has none)."""
    printers = (await conn.execute(text(
        "SELECT id, model_uuid, machine_profile_override, nozzle_variant, preferred_slicer_plugin_id FROM printers"))).fetchall()
    for pid, model_uuid, override, variant, preferred in printers:
        if override or not model_uuid:
            continue
        profile = await _profile_for(conn, model_uuid, preferred or PROVIDER, json.loads(variant) if variant else None)
        if profile:
            await conn.execute(text("UPDATE printers SET machine_profile_override = :p WHERE id = :i"), {"p": profile, "i": pid})
    await conn.execute(text("ALTER TABLE printers RENAME COLUMN machine_profile_override TO current_orca_printer_profile"))
    if "nozzle_variant" in await _columns(conn, "printers"):
        await conn.execute(text("ALTER TABLE printers DROP COLUMN nozzle_variant"))

    await conn.execute(text("DROP INDEX IF EXISTS ix_job_model_targets_model_uuid"))
    await conn.execute(text("ALTER TABLE job_model_targets RENAME TO job_model_targets_new"))
    await conn.execute(text(_TARGETS_DDL.format(key_col="machine_profile VARCHAR(255) NOT NULL")))
    for row in (await conn.execute(text(f"SELECT id, model_uuid, {_TARGET_COLS} FROM job_model_targets_new"))).mappings().all():
        profile = await _profile_for(conn, row["model_uuid"], PROVIDER, None)
        if profile is None:
            continue
        await conn.execute(text(
            f"INSERT INTO job_model_targets (id, machine_profile, {_TARGET_COLS}) "
            "VALUES (:id, :mp, :job_id, :print_profile, :filament_profile, :filament_id, :material_provider, :material_ref,"
            " :filament_type, :filament_color, :filament_map)"),
            {"id": row["id"], "mp": profile,
             **{k: row[k] for k in ("job_id", "print_profile", "filament_profile", "filament_id", "material_provider",
                                    "material_ref", "filament_type", "filament_color", "filament_map")}})
    await conn.execute(text("DROP TABLE job_model_targets_new"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_model_targets_job_id ON job_model_targets (job_id)"))
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_job_model_targets_machine_profile ON job_model_targets (machine_profile)"))
    await conn.execute(text("DROP TABLE IF EXISTS printer_model_slicer_profiles"))
