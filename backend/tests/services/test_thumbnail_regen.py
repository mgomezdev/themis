import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models import UploadedFile
from app.services import thumbnail_regen

FILE_ID = 999_999_001


@pytest.mark.asyncio
async def test_regen_file_thumbnails_uses_injected_session_factory(monkeypatch, tmp_path):
    """regen_file_thumbnails must go through the session factory it's told to use,
    not the real app database - otherwise a fresh checkout/worktree/CI with no
    pre-migrated data/themis.db fails here, and a test pointed at an isolated DB
    silently no-ops against the wrong one instead of touching the seeded row."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        session.add(UploadedFile(
            id=FILE_ID,
            original_filename="test.3mf",
            relative_path="test.3mf",
            plates=[{"plate_number": 1}],
            uploaded_at="2026-01-01T00:00:00",
        ))
        await session.commit()

    monkeypatch.setattr(thumbnail_regen, "get_filecache_dir", lambda: tmp_path)
    monkeypatch.setattr(thumbnail_regen, "_regen_sync", lambda *a, **k: {1: "/fake/plate_1.png"})

    original_factory = thumbnail_regen._session_factory
    thumbnail_regen.set_session_factory(factory)
    try:
        await thumbnail_regen.regen_file_thumbnails(FILE_ID)

        async with factory() as session:
            updated = await session.get(UploadedFile, FILE_ID)
            assert updated.plates[0]["thumbnail_path"] == "/fake/plate_1.png"
    finally:
        thumbnail_regen.set_session_factory(original_factory)
        await engine.dispose()


# ---------------------------------------------------------------------------
# regen_file_thumbnails — what gets rendered and what gets stored
# ---------------------------------------------------------------------------

import io
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch


@pytest.fixture
async def regen_env(monkeypatch, tmp_path, session_factory):
    """The injected session factory, an isolated library/filecache, and a recording fake `_regen_sync`."""
    library, cache = tmp_path / "library", tmp_path / "cache"
    monkeypatch.setattr(thumbnail_regen, "get_library_dir", lambda: library)
    monkeypatch.setattr(thumbnail_regen, "get_filecache_dir", lambda: cache)
    calls: list[tuple] = []
    result: dict[int, str] = {}

    def fake_sync(stored_path, plate_numbers, thumb_dir):
        calls.append((stored_path, list(plate_numbers), thumb_dir))
        return dict(result)

    monkeypatch.setattr(thumbnail_regen, "_regen_sync", fake_sync)
    original = thumbnail_regen._session_factory
    thumbnail_regen.set_session_factory(session_factory)

    async def add_file(plates, relative_path="Parts/model.3mf", file_id=1):
        async with session_factory() as s:
            s.add(UploadedFile(id=file_id, original_filename="model.3mf", relative_path=relative_path,
                               plates=plates, uploaded_at="2026-01-01T00:00:00"))
            await s.commit()

    async def plates_of(file_id=1):
        async with session_factory() as s:
            return (await s.get(UploadedFile, file_id)).plates

    from types import SimpleNamespace
    yield SimpleNamespace(add_file=add_file, plates_of=plates_of, calls=calls, result=result,
                          library=library, cache=cache)
    thumbnail_regen.set_session_factory(original)


async def test_regen_renders_only_the_plates_without_a_thumbnail_and_keeps_the_rest(regen_env):
    await regen_env.add_file([
        {"plate_number": 1, "thumbnail_path": "/already/plate_1.png", "estimated_time": 60},
        {"plate_number": 2, "estimated_time": 90},
        {"plate_number": 3, "thumbnail_path": None},
        {"plate_number": 4},
    ])
    regen_env.result.update({2: "/new/plate_2.png", 3: "/new/plate_3.png"})  # plate 4 fails to render

    await thumbnail_regen.regen_file_thumbnails(1)

    (stored_path, plate_numbers, thumb_dir), = regen_env.calls
    assert stored_path == str(regen_env.library / "Parts" / "model.3mf")
    assert plate_numbers == [2, 3, 4]
    assert Path(thumb_dir) == regen_env.cache / "1" / "thumbnails" and Path(thumb_dir).is_dir()
    assert await regen_env.plates_of() == [
        {"plate_number": 1, "thumbnail_path": "/already/plate_1.png", "estimated_time": 60},   # untouched
        {"plate_number": 2, "estimated_time": 90, "thumbnail_path": "/new/plate_2.png"},       # other keys kept
        {"plate_number": 3, "thumbnail_path": "/new/plate_3.png"},
        {"plate_number": 4, "thumbnail_path": None},                                          # failed: still missing
    ]


async def test_regen_changes_nothing_when_no_plate_could_be_rendered(regen_env):
    original = [{"plate_number": 1}, {"plate_number": 2}]
    await regen_env.add_file(original)

    await thumbnail_regen.regen_file_thumbnails(1)  # fake returns {}

    assert len(regen_env.calls) == 1
    assert await regen_env.plates_of() == original


@pytest.mark.parametrize("plates, relative_path", [
    ([{"plate_number": 1, "thumbnail_path": "/have/it.png"}], "a.3mf"),   # everything already rendered
    ([], "a.3mf"),                                                       # no plates
    ([{"plate_number": 1}], ""),                                         # not a library-indexed file
])
async def test_regen_skips_files_with_nothing_to_render(regen_env, plates, relative_path):
    await regen_env.add_file(plates, relative_path=relative_path)

    await thumbnail_regen.regen_file_thumbnails(1)

    assert regen_env.calls == []


async def test_regen_ignores_an_unknown_file_id(regen_env):
    await thumbnail_regen.regen_file_thumbnails(424242)
    assert regen_env.calls == []


# ---------------------------------------------------------------------------
# _regen_sync / _render_plate_thumbnail — the OrcaSlicer boundary
# ---------------------------------------------------------------------------

def test_regen_sync_skips_everything_when_orca_is_not_configured(tmp_path):
    with patch.object(thumbnail_regen, "get_orca_executable", side_effect=RuntimeError("no orca")), \
         patch.object(thumbnail_regen, "_render_plate_thumbnail") as render:
        assert thumbnail_regen._regen_sync("a.3mf", [1, 2], str(tmp_path)) == {}
    render.assert_not_called()


def test_regen_sync_returns_only_the_plates_that_rendered(tmp_path):
    def render(orca, stored_path, num, thumb_dir):
        assert (orca, stored_path, thumb_dir) == ("/opt/orca", "a.3mf", tmp_path)
        return f"/out/plate_{num}.png" if num != 2 else None

    with patch.object(thumbnail_regen, "get_orca_executable", return_value="/opt/orca"), \
         patch.object(thumbnail_regen, "_render_plate_thumbnail", render):
        assert thumbnail_regen._regen_sync("a.3mf", [1, 2, 3], str(tmp_path)) == {
            1: "/out/plate_1.png", 3: "/out/plate_3.png"}


def _fake_orca(entries: dict[str, bytes] | None, seen: list | None = None, produce=True):
    """A stand-in for subprocess.run that 'exports' a 3MF containing `entries` to the --export-3mf path."""
    def run(cmd, **kwargs):
        if seen is not None:
            seen.append((cmd, kwargs))
        if produce:
            out = Path(cmd[cmd.index("--export-3mf") + 1])
            with zipfile.ZipFile(out, "w") as zf:
                for name, data in (entries or {}).items():
                    zf.writestr(name, data)
        return MagicMock(returncode=0)
    return run


def test_render_runs_orca_for_one_unarranged_plate_and_saves_its_thumbnail(tmp_path):
    seen: list = []
    with patch.object(thumbnail_regen.subprocess, "run", _fake_orca({"Metadata/plate_2.png": b"PNG2"}, seen)):
        saved = thumbnail_regen._render_plate_thumbnail("/opt/orca", "/lib/a.3mf", 2, tmp_path)

    assert saved == str(tmp_path / "plate_2.png")
    assert (tmp_path / "plate_2.png").read_bytes() == b"PNG2"
    (cmd, kwargs), = seen
    assert cmd[:3] == ["/opt/orca", "--slice", "2"] and cmd[-1] == "/lib/a.3mf"
    assert cmd[cmd.index("--arrange") + 1] == "0"  # render the plate as-is, never re-arrange
    assert kwargs["timeout"] == thumbnail_regen._TIMEOUT and kwargs["capture_output"] is True


def test_render_falls_back_to_plate_1_because_orca_renumbers_single_plate_exports(tmp_path):
    with patch.object(thumbnail_regen.subprocess, "run", _fake_orca({"Metadata/plate_1.png": b"ONLY"})):
        saved = thumbnail_regen._render_plate_thumbnail("/opt/orca", "/lib/a.3mf", 3, tmp_path)

    assert saved == str(tmp_path / "plate_3.png")  # stored under the REQUESTED plate number
    assert (tmp_path / "plate_3.png").read_bytes() == b"ONLY"


def test_render_prefers_the_requested_plate_over_plate_1(tmp_path):
    entries = {"Metadata/plate_1.png": b"ONE", "Metadata/plate_2.png": b"TWO"}
    with patch.object(thumbnail_regen.subprocess, "run", _fake_orca(entries)):
        thumbnail_regen._render_plate_thumbnail("/opt/orca", "/lib/a.3mf", 2, tmp_path)

    assert (tmp_path / "plate_2.png").read_bytes() == b"TWO"


@pytest.mark.parametrize("runner, logged", [
    (_fake_orca({"Metadata/other.png": b"x"}), "No plate thumbnail in output"),   # 3MF without a plate thumbnail
    (_fake_orca(None, produce=False), "produced no output"),                       # orca wrote nothing
    (MagicMock(side_effect=subprocess.TimeoutExpired("orca", 120)), "Thumbnail regen timed out for"),  # hung
    (MagicMock(side_effect=OSError("exec format error")), "Thumbnail regen error"),  # could not start
])
def test_render_returns_none_on_any_failure_saves_nothing_and_says_why(tmp_path, caplog, runner, logged):
    with patch.object(thumbnail_regen.subprocess, "run", runner), caplog.at_level("DEBUG", logger=thumbnail_regen.logger.name):
        assert thumbnail_regen._render_plate_thumbnail("/opt/orca", "/lib/a.3mf", 1, tmp_path) is None
    assert list(tmp_path.iterdir()) == []
    assert logged in caplog.text


def test_render_returns_none_when_the_exported_3mf_is_corrupt(tmp_path, caplog):
    def run(cmd, **kwargs):
        Path(cmd[cmd.index("--export-3mf") + 1]).write_bytes(b"not a zip")
        return MagicMock(returncode=0)

    with patch.object(thumbnail_regen.subprocess, "run", run), caplog.at_level("DEBUG", logger=thumbnail_regen.logger.name):
        assert thumbnail_regen._render_plate_thumbnail("/opt/orca", "/lib/a.3mf", 1, tmp_path) is None
    assert "Failed to extract thumbnail" in caplog.text
