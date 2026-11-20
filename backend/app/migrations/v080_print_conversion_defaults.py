"""Store format-pair defaults and brand-level converter preferences."""
from sqlalchemy import text

version = 80
name = "print_conversion_defaults"


async def up(conn) -> None:
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS print_conversion_defaults (
            input_kind VARCHAR(24) NOT NULL, output_kind VARCHAR(24) NOT NULL,
            provider_plugin_id VARCHAR(64) NOT NULL, PRIMARY KEY (input_kind, output_kind)
        )
    """))
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS brand_conversion_preferences (
            manufacturer_id VARCHAR(64) PRIMARY KEY NOT NULL, provider_plugin_id VARCHAR(64) NOT NULL
        )
    """))


async def down(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS brand_conversion_preferences"))
    await conn.execute(text("DROP TABLE IF EXISTS print_conversion_defaults"))
