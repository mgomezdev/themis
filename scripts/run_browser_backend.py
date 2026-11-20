"""Isolated real-backend browser harness (BIZ-307); never reads a deployment DB."""
import asyncio
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import uvicorn


async def prepare():
    from app.database import init_db, SessionLocal, engine
    from app.models import PluginConfig
    from app.plugins import registered_plugins
    await init_db()
    # No remote services on boot. The browser enables required plugins via REST.
    async with SessionLocal() as session:
        for manifest in registered_plugins():
            row = await session.get(PluginConfig, manifest.id)
            if row is None:
                session.add(PluginConfig(plugin_id=manifest.id, enabled=False, settings={}, secrets={}, state={}))
            else:
                row.enabled = False
        await session.commit()
    await engine.dispose()


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / 'backend'))
    with TemporaryDirectory(prefix='themis-browser-') as data:
        # Deliberately override inherited deployment paths and credentials.
        os.environ['THEMIS_DATA_DIR'] = data
        os.environ['THEMIS_LIBRARY_DIR'] = str(Path(data) / 'library')
        os.environ['THEMIS_STATIC_DIR'] = str(root / 'frontend' / 'dist')
        os.environ['THEMIS_BOOTSTRAP_KEY'] = 'isolated-browser-test-key'
        os.environ['THEMIS_LOCAL_NETWORKS'] = '127.0.0.0/8'
        asyncio.run(prepare())
        uvicorn.run('app.main:app', host='127.0.0.1', port=18071, log_level='warning')
