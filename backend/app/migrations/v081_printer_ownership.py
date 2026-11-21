"""Atomic ownership guards, preserving old rows for explicit user reconciliation."""
from sqlalchemy import text

version = 81
name = "printer_ownership"


def _host(row):
    return f"lower(rtrim(trim(coalesce(nullif(json_extract({row}.connection_config, '$.ip_address'), ''), json_extract({row}.connection_config, '$.host'), '')), '.'))"


def _serial(row):
    return f"lower(trim(coalesce(json_extract({row}.connection_config, '$.serial_number'), '')))"


async def up(conn):
    for action, suffix in (("INSERT", "insert"), ("UPDATE OF connection_config, plugin_id", "update")):
        # Metadata-only updates remain possible for pre-existing conflicting rows.
        changed = "" if suffix == "insert" else "AND (NEW.connection_config IS NOT OLD.connection_config OR NEW.plugin_id IS NOT OLD.plugin_id)"
        await conn.execute(text(f"""
            CREATE TRIGGER IF NOT EXISTS printer_ownership_{suffix}
            BEFORE {action} ON printers
            WHEN EXISTS (
                SELECT 1 FROM printers AS p WHERE p.id != NEW.id AND (
                    ({_host('NEW')} != '' AND {_host('NEW')} = {_host('p')}) OR
                    ({_serial('NEW')} != '' AND {_serial('NEW')} = {_serial('p')})
                )
            ) {changed}
            BEGIN SELECT RAISE(ABORT, 'printer ownership conflict'); END
        """))


async def down(conn):
    for suffix in ("insert", "update"):
        await conn.execute(text(f"DROP TRIGGER IF EXISTS printer_ownership_{suffix}"))
