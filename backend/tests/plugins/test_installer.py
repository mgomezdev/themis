"""Plugin installation pipeline (BIZ-223): safe extraction, validation, subprocess dry-run, commit/upgrade/rollback/uninstall,
GitHub fetch (stubbed HTTP). A failed install must leave nothing on disk and no row."""
import json
import stat
import tarfile
import zipfile

import httpx
import pytest
from sqlalchemy import select, text

from app.models import AuditLog, InstalledPlugin
from app.plugins import installer
from app.plugins.installer import InstallError
from tests.plugins import pkg_builder as pb


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("THEMIS_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    return tmp_path / "data"


async def _bytes(b: bytes):
    yield b


async def stage(b: bytes, name="plugin.zip"):
    return await installer.stage_upload(_bytes(b), name)


async def stage_commit(b: bytes, session_factory, name="plugin.zip"):
    """The whole pipeline: static checks at stage, the import check at commit (after the admin's confirmation)."""
    staged = await stage(b, name)
    async with session_factory() as s:
        return await installer.commit(s, staged.token, actor="a")


def assert_clean(data_dir):
    """Nothing installed and nothing left in staging."""
    plugins = data_dir / "plugins"
    if plugins.exists():
        assert [p.name for p in plugins.iterdir() if p.name != ".staging"] == []
        staging = plugins / ".staging"
        assert not staging.exists() or list(staging.iterdir()) == []


# ---- the happy path ----------------------------------------------------------------------------------------------------

async def test_zip_and_tgz_install_end_to_end(data_dir, session_factory):
    for make, name in ((pb.make_zip, "a.zip"), (pb.make_tgz, "a.tgz")):
        staged = await stage(make(pb.files("acme_inv")), name)
        assert staged.preview()["publisher"] == "Acme" and len(staged.archive_sha256) == 64
        async with session_factory() as s:
            row = await installer.commit(s, staged.token, actor="local-admin")
        assert (row.status, row.version, row.source) == ("pending_restart", "1.0.0", "upload")
        assert (data_dir / "plugins/acme_inv/1.0.0/themis-plugin.toml").is_file()
        assert (data_dir / "plugins/.staging" / staged.token).exists() is False
        async with session_factory() as s:                                   # reinstalling the same version is refused
            await s.delete(await s.get(InstalledPlugin, "acme_inv"))
            await s.commit()
        import shutil
        shutil.rmtree(data_dir / "plugins/acme_inv")
    async with session_factory() as s:
        log = (await s.execute(select(AuditLog))).scalars().all()
        assert [(r.action, r.target, r.actor) for r in log] == [("plugin.install", "acme_inv", "local-admin")] * 2
        assert log[0].detail["archive_sha256"]


async def test_a_single_top_level_folder_and_a_subdirectory_are_found(data_dir, session_factory):
    s1 = await stage(pb.make_zip(pb.files("acme_inv"), prefix="acme-main/"))
    assert s1.toml.id == "acme_inv"
    installer.discard(s1.token)
    mono = {**{f"other/{k}": v for k, v in pb.files("zzz_other").items()}, **{f"plugins/inv/{k}": v for k, v in pb.files("acme_inv").items()}}
    from app.plugins.installer import extract_archive, locate_root
    arc = data_dir / "m.zip"
    arc.write_bytes(pb.make_zip(mono, prefix="mono-abc/"))
    out = data_dir / "out"
    extract_archive(arc, out)
    assert locate_root(out, "plugins/inv").name == "inv"
    with pytest.raises(InstallError, match="not found"):
        locate_root(out, "nope")
    with pytest.raises(InstallError, match="unsafe"):
        locate_root(out, "../x")


# ---- rejection: a failed install leaves nothing --------------------------------------------------------------------------

def _link_zip(kind: int) -> bytes:
    info = zipfile.ZipInfo("acme_inv/link")
    info.external_attr = (kind | 0o777) << 16
    return pb.make_zip_raw([(zipfile.ZipInfo("themis-plugin.toml"), pb.files()["themis-plugin.toml"].encode()), (info, b"/etc/passwd")])


def _tar_with(info: tarfile.TarInfo) -> bytes:
    return pb.make_tgz(pb.files(), extra=[info])


def _sym():
    i = tarfile.TarInfo("acme_inv/l"); i.type = tarfile.SYMTYPE; i.linkname = "/etc/passwd"; return i


def _hard():
    i = tarfile.TarInfo("acme_inv/h"); i.type = tarfile.LNKTYPE; i.linkname = "themis-plugin.toml"; return i


def _dev():
    i = tarfile.TarInfo("acme_inv/d"); i.type = tarfile.CHRTYPE; return i


REJECTS = [
    ("zip-slip", lambda: pb.make_zip({**pb.files(), "../evil.py": "x"}), "unsafe path"),
    ("absolute path", lambda: pb.make_zip({**pb.files(), "/etc/evil.py": "x"}), "unsafe path"),
    ("backslash", lambda: pb.make_zip({**pb.files(), "a\\..\\evil.py": "x"}), "unsafe path"),
    ("tar slip", lambda: pb.make_tgz({**pb.files(), "../evil.py": "x"}), "unsafe path"),
    ("zip symlink", lambda: _link_zip(stat.S_IFLNK), "links and special files"),
    ("zip device", lambda: _link_zip(stat.S_IFCHR), "links and special files"),
    ("tar symlink", lambda: _tar_with(_sym()), "links and special files"),
    ("tar hardlink", lambda: _tar_with(_hard()), "links and special files"),
    ("tar device", lambda: _tar_with(_dev()), "links and special files"),
    ("not an archive", lambda: b"hello world, definitely not an archive", "not a .zip"),
    ("corrupt zip", lambda: b"PK\x03\x04" + b"junk" * 10, "corrupt"),
    ("no toml", lambda: pb.make_zip({"acme_inv/__init__.py": ""}), "themis-plugin.toml not found"),
    ("bad toml", lambda: pb.make_zip({**pb.files(), "themis-plugin.toml": "id = ["}), "not valid TOML"),
    ("bad id", lambda: pb.make_zip(pb.files("A-b")), "must match"),
    ("reserved bundled id", lambda: pb.make_zip(pb.files("spoolman")), "reserved"),
    ("host_api mismatch", lambda: pb.make_zip(pb.files(host_api=2, mhost=1)), "host_api 2"),
    ("unknown kind", lambda: pb.make_zip(pb.files(kind="printer_vendor")), "unknown kind"),
    ("bad version", lambda: pb.make_zip(pb.files(version="../1")), "semver"),
    ("unknown toml key", lambda: pb.make_zip({**pb.files(), "themis-plugin.toml": pb.files()["themis-plugin.toml"] + "evil = 1\n"}), "unknown keys"),
    ("bad entry", lambda: pb.make_zip({**pb.files(), "themis-plugin.toml": pb.files()["themis-plugin.toml"].replace("acme_inv:MANIFEST", "nocolon")}), "entry"),
    ("entry module missing", lambda: pb.make_zip({k: v for k, v in pb.files().items() if not k.startswith("acme_inv/")}), "not found in the package"),
    ("import error", lambda: pb.make_zip(pb.files(code="import definitely_not_installed_xyz\n")), "failed to import"),
    ("not a manifest", lambda: pb.make_zip(pb.files(code="MANIFEST = 3\n")), "failed to import|PluginManifest"),
    ("manifest disagrees with toml", lambda: pb.make_zip(pb.files(mversion="9.9.9")), "does not match"),
    ("shadows a Themis package", lambda: pb.make_zip(pb.files(pkg="app")), "already used"),
    ("shadows the stdlib", lambda: pb.make_zip(pb.files(pkg="json")), "already used"),
    ("min_themis too new", lambda: pb.make_zip({**pb.files(), "themis-plugin.toml": pb.files()["themis-plugin.toml"] + 'min_themis = "9999.1"\n'}), "requires Themis"),
]


@pytest.mark.parametrize("label,build,message", REJECTS, ids=[r[0] for r in REJECTS])
async def test_rejected_archives_leave_nothing_behind(data_dir, session_factory, label, build, message):
    with pytest.raises(InstallError, match=message):
        await stage_commit(build(), session_factory)
    assert_clean(data_dir)
    async with session_factory() as s:
        assert (await s.execute(select(InstalledPlugin))).first() is None


async def test_limits(data_dir, monkeypatch):
    monkeypatch.setattr(installer, "MAX_FILES", 2)
    with pytest.raises(InstallError, match="more than 2 files"):
        await stage(pb.make_zip(pb.files()))
    with pytest.raises(InstallError, match="more than 2 files"):
        await stage(pb.make_tgz(pb.files()))
    monkeypatch.setattr(installer, "MAX_FILES", 3000)
    monkeypatch.setattr(installer, "MAX_UNCOMPRESSED_BYTES", 50)
    with pytest.raises(InstallError, match="expands to more than"):
        await stage(pb.make_zip(pb.files()))
    with pytest.raises(InstallError, match="expands to more than"):
        await stage(pb.make_tgz(pb.files()))
    monkeypatch.setattr(installer, "MAX_UNCOMPRESSED_BYTES", 80 * 1024 * 1024)
    monkeypatch.setattr(installer, "MAX_ARCHIVE_BYTES", 100)
    with pytest.raises(InstallError, match="larger than"):
        await stage(pb.make_zip(pb.files()))
    with pytest.raises(InstallError, match="empty"):
        await stage(b"")
    assert_clean(data_dir)


async def test_directory_entries_and_oversized_headers_count_against_the_limits(data_dir, monkeypatch):
    import io
    monkeypatch.setattr(installer, "MAX_FILES", 5)
    dirs = [tarfile.TarInfo(f"d{i}") for i in range(10)]
    for d in dirs:
        d.type = tarfile.DIRTYPE
    with pytest.raises(InstallError, match="more than 5 files"):
        await stage(pb.make_tgz(pb.files(), extra=dirs))
    zbuf = pb.make_zip_raw([(zipfile.ZipInfo(f"d{i}/"), b"") for i in range(10)])
    with pytest.raises(InstallError, match="more than 5 files"):
        await stage(zbuf)
    monkeypatch.setattr(installer, "MAX_FILES", 3000)
    # a PAX record is header data, not file bytes: the decompressed cap still catches an archive that is mostly headers
    monkeypatch.setattr(installer, "MAX_UNCOMPRESSED_BYTES", 1024 * 1024)
    info = tarfile.TarInfo("themis-plugin.toml")
    info.pax_headers = {"comment": "x" * (6 * 1024 * 1024)}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tf:
        tf.addfile(info, io.BytesIO(b""))
    assert len(buf.getvalue()) < 100_000                                    # tiny on the wire, 6 MB of header once decompressed
    with pytest.raises(InstallError, match="expands to more than"):
        await stage(buf.getvalue())
    assert_clean(data_dir)


async def test_a_header_that_lies_about_size_cannot_bypass_the_cap(data_dir, monkeypatch):
    """The cap is enforced on bytes actually written, not on the sizes an archive declares."""
    monkeypatch.setattr(installer, "MAX_UNCOMPRESSED_BYTES", 1000)
    big = pb.make_tgz({**pb.files(), "big.bin": "x" * 5000})
    with pytest.raises(InstallError, match="expands to more than"):
        await stage(big)
    assert_clean(data_dir)


async def test_dry_run_does_not_touch_this_process(data_dir, session_factory):
    import sys
    before = list(sys.path)
    await stage_commit(pb.make_zip(pb.files()), session_factory)
    assert sys.path == before and "acme_inv" not in sys.modules


async def test_a_preview_runs_none_of_the_packages_code_only_the_confirmed_commit_does(data_dir, session_factory):
    """Previews (and update checks) must not execute code the admin has not agreed to trust yet."""
    marker = data_dir / "ran.txt"
    code = f"open({str(marker)!r}, 'w').write('x')\n" + pb.CODE.format(id="acme_inv", name="A", mversion="1.0.0", mhost=1, tab="default", extra="")
    staged = await stage(pb.make_zip(pb.files(code=code)))
    assert not marker.exists()                                  # staged + validated + previewed: nothing ran
    async with session_factory() as s:
        await installer.commit(s, staged.token, actor="a")
    assert marker.exists()                                      # the import check at commit is the first execution


async def test_a_package_that_fails_its_import_check_at_commit_is_dropped(data_dir, session_factory):
    staged = await stage(pb.make_zip(pb.files(code="raise RuntimeError('boom')\n")))     # statically fine, so the preview shows
    async with session_factory() as s:
        with pytest.raises(InstallError, match="boom"):
            await installer.commit(s, staged.token, actor="a")
    assert_clean(data_dir)


async def test_vendor_dir_is_on_the_dry_run_path(data_dir, session_factory):
    code = "import vendored_lib\n" + pb.CODE.format(id="acme_inv", name="A", mversion="1.0.0", mhost=1, tab="default", extra="")
    row = await stage_commit(pb.make_zip({**pb.files(code=code), "vendor/vendored_lib.py": "X = 1\n"}), session_factory)
    assert row.plugin_id == "acme_inv"


# ---- commit / upgrade / rollback / uninstall ---------------------------------------------------------------------------

async def _install(session_factory, version="1.0.0", **kw):
    staged = await stage(pb.make_zip(pb.files("acme_inv", version, **kw)))
    async with session_factory() as s:
        return await installer.commit(s, staged.token, actor="a")


async def test_upgrade_keeps_the_running_version_and_prunes_the_rest(data_dir, session_factory):
    await _install(session_factory, "1.0.0")
    async with session_factory() as s:                                     # the restart happened: 1.0.0 is running
        (await s.get(InstalledPlugin, "acme_inv")).status = "active"
        await s.commit()
    row = await _install(session_factory, "1.1.0")
    assert (row.version, row.previous_version, row.status) == ("1.1.0", "1.0.0", "pending_restart")
    # a second upgrade BEFORE the restart: 1.1.0 never ran, so the running 1.0.0 must stay (code it imported, rollback target)
    row = await _install(session_factory, "1.2.0")
    assert (row.version, row.previous_version) == ("1.2.0", "1.0.0")
    assert sorted(p.name for p in (data_dir / "plugins/acme_inv").iterdir()) == ["1.0.0", "1.2.0"]
    async with session_factory() as s:
        assert [r.action for r in (await s.execute(select(AuditLog))).scalars()] == ["plugin.install", "plugin.upgrade", "plugin.upgrade"]
        (await s.get(InstalledPlugin, "acme_inv")).status = "active"       # restarted again
        await s.commit()
    row = await _install(session_factory, "1.3.0")
    assert (row.previous_version, sorted(p.name for p in (data_dir / "plugins/acme_inv").iterdir())) == ("1.2.0", ["1.2.0", "1.3.0"])


async def test_upgrading_a_never_started_install_leaves_no_dead_version(data_dir, session_factory):
    await _install(session_factory, "1.0.0")
    row = await _install(session_factory, "1.1.0")                         # 1.0.0 never ran: nothing to keep for rollback
    assert row.previous_version is None
    assert sorted(p.name for p in (data_dir / "plugins/acme_inv").iterdir()) == ["1.1.0"]


async def test_a_failed_commit_leaves_no_directory_and_no_row_and_the_version_can_be_retried(data_dir, session_factory, monkeypatch):
    import shutil
    from app.services import audit
    real_move = shutil.move
    real_record = audit.record

    def partial_move(src, dst):                                            # disk full midway: the target exists but is incomplete
        import os
        os.makedirs(dst)
        raise OSError(28, "No space left on device")
    staged = await stage(pb.make_zip(pb.files()))
    monkeypatch.setattr(installer.shutil, "move", partial_move)
    async with session_factory() as s:
        with pytest.raises(InstallError, match="could not install"):
            await installer.commit(s, staged.token, actor="a")
    monkeypatch.setattr(installer.shutil, "move", real_move)
    assert_clean(data_dir)
    # a DB failure after the move also removes the moved directory
    staged = await stage(pb.make_zip(pb.files()))
    async def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(audit, "record", boom)
    async with session_factory() as s:
        with pytest.raises(RuntimeError, match="db down"):
            await installer.commit(s, staged.token, actor="a")
    assert_clean(data_dir)
    async with session_factory() as s:
        assert (await s.execute(select(InstalledPlugin))).first() is None
    monkeypatch.setattr(audit, "record", real_record)                      # (not monkeypatch.undo(): that would also drop the data_dir env)
    assert (await _install(session_factory)).version == "1.0.0"            # "already installed" never wedges the version


async def test_two_confirmations_of_one_token_install_once_and_the_loser_gets_a_clean_error(data_dir, session_factory):
    import asyncio
    staged = await stage(pb.make_zip(pb.files()))

    async def go():
        async with session_factory() as s:
            return await installer.commit(s, staged.token, actor="a")
    results = await asyncio.gather(go(), go(), return_exceptions=True)
    assert sum(isinstance(r, InstalledPlugin) for r in results) == 1
    loser = next(r for r in results if not isinstance(r, InstalledPlugin))
    assert isinstance(loser, InstallError) and "unknown or expired" in str(loser)


async def test_abandoned_previews_expire(data_dir):
    import os
    import time
    old = await stage(pb.make_zip(pb.files("old_one")))
    fresh = await stage(pb.make_zip(pb.files("new_one")))
    stale = time.time() - installer.STAGING_TTL_S - 60
    os.utime(data_dir / "plugins/.staging" / old.token, (stale, stale))
    await stage(pb.make_zip(pb.files("third_one")))                       # any new staging sweeps the expired ones
    with pytest.raises(InstallError, match="unknown or expired"):
        installer.load_staged(old.token)
    assert installer.load_staged(fresh.token).toml.id == "new_one"


async def test_same_version_is_refused_and_leaves_the_original_intact(data_dir, session_factory):
    await _install(session_factory, "1.0.0")
    staged = await stage(pb.make_zip(pb.files("acme_inv", "1.0.0", name="Changed")))
    async with session_factory() as s:
        with pytest.raises(InstallError, match="already installed"):
            await installer.commit(s, staged.token, actor="a")
        assert (await s.get(InstalledPlugin, "acme_inv")).name == "Acme inventory"


async def test_commit_rejects_unknown_tokens_and_a_mismatched_expected_id(data_dir, session_factory):
    async with session_factory() as s:
        for bad in ("nope", "../../etc", "0" * 32):
            with pytest.raises(InstallError, match="unknown or expired"):
                await installer.commit(s, bad, actor="a")
        staged = await stage(pb.make_zip(pb.files("acme_inv")))
        with pytest.raises(InstallError, match="not 'other_inv'"):
            await installer.commit(s, staged.token, actor="a", expect_id="other_inv")


async def _started(session_factory, plugin_id="acme_inv"):
    """The restart happened: the installed version is running (so an upgrade keeps it as the rollback target)."""
    async with session_factory() as s:
        (await s.get(InstalledPlugin, plugin_id)).status = "active"
        await s.commit()


async def test_rollback_swaps_versions_and_refuses_across_a_migration(data_dir, session_factory):
    await _install(session_factory, "1.0.0")
    await _started(session_factory)
    await _install(session_factory, "1.1.0")
    async with session_factory() as s:
        row = await installer.rollback(s, "acme_inv", actor="a")
        assert (row.version, row.previous_version, row.status) == ("1.0.0", "1.1.0", "pending_restart")
        await installer.rollback(s, "acme_inv", actor="a")                 # and forward again
        # the DB has a migration that the version we would return to does not ship
        await s.execute(text("INSERT INTO plugin_schema_versions (plugin_id, version, name) VALUES ('acme_inv', 2, 'x')"))
        await s.commit()
        with pytest.raises(InstallError, match=r"migration\(s\) \[2\]"):
            await installer.rollback(s, "acme_inv", actor="a")
        assert (await s.get(InstalledPlugin, "acme_inv")).version == "1.1.0"


async def test_rollback_needs_a_previous_version(data_dir, session_factory):
    await _install(session_factory)
    await _started(session_factory)
    async with session_factory() as s:
        with pytest.raises(InstallError, match="no previous version"):
            await installer.rollback(s, "acme_inv", actor="a")
        with pytest.raises(InstallError, match="not an installed plugin"):
            await installer.rollback(s, "spoolman", actor="a")


async def test_uninstall_marks_pending_removal_and_blocks_a_reinstall_until_restart(data_dir, session_factory):
    await _install(session_factory)
    async with session_factory() as s:
        row = await installer.mark_uninstall(s, "acme_inv", actor="a", removed_data=False)
        assert row.status == "pending_removal"
    assert (data_dir / "plugins/acme_inv/1.0.0").is_dir()                  # code goes at the restart, not now
    staged = await stage(pb.make_zip(pb.files("acme_inv", "2.0.0")))
    async with session_factory() as s:
        with pytest.raises(InstallError, match="waiting to be uninstalled"):
            await installer.commit(s, staged.token, actor="a")


# ---- GitHub (stubbed HTTP) ---------------------------------------------------------------------------------------------

SHA1, SHA2 = "a" * 40, "b" * 40


class FakeGithub:
    def __init__(self, monkeypatch, versions: dict[str, bytes]):
        self.requests: list[str] = []
        self.versions = versions                       # sha -> tarball
        self.heads = {"main": SHA1, "v1": SHA1}
        self.status = 200
        monkeypatch.setattr(installer, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(self.handle)))

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.requests.append(f"{url.host}{url.path}")
        if self.status != 200:
            return httpx.Response(self.status)
        if url.host == "api.github.com" and url.path == "/repos/acme/inv":
            return httpx.Response(200, json={"default_branch": "main"})
        if url.host == "api.github.com" and url.path.startswith("/repos/acme/inv/commits/"):
            ref = url.path.split("/commits/", 1)[1]
            return httpx.Response(200, json={"sha": self.heads[ref]}) if ref in self.heads else httpx.Response(404)
        if url.host == "codeload.github.com" and url.path.startswith("/acme/inv/tar.gz/"):
            sha = url.path.rsplit("/", 1)[1]
            return httpx.Response(200, content=self.versions[sha]) if sha in self.versions else httpx.Response(404)
        return httpx.Response(500)


def tgz(version):
    return pb.make_tgz(pb.files("acme_inv", version), prefix="inv-abc/")


async def test_github_install_downloads_the_resolved_sha_not_the_ref(data_dir, session_factory, monkeypatch):
    gh = FakeGithub(monkeypatch, {SHA1: tgz("1.0.0")})
    staged = await installer.stage_github("https://github.com/acme/inv.git")           # no ref: the default branch
    assert gh.requests == ["api.github.com/repos/acme/inv", "api.github.com/repos/acme/inv/commits/main", f"codeload.github.com/acme/inv/tar.gz/{SHA1}"]
    assert (staged.ref, staged.commit_sha, staged.source) == ("main", SHA1, "github")
    async with session_factory() as s:
        row = await installer.commit(s, staged.token, actor="a")
    assert (row.source_url, row.commit_sha, row.ref) == ("https://github.com/acme/inv", SHA1, "main")


async def test_github_subdir_and_explicit_ref(data_dir, monkeypatch):
    mono = pb.make_tgz({f"plugins/x/{k}": v for k, v in pb.files("acme_inv").items()}, prefix="inv-abc/")
    gh = FakeGithub(monkeypatch, {SHA1: mono})
    staged = await installer.stage_github("https://github.com/acme/inv", "v1", "plugins/x")
    assert staged.toml.id == "acme_inv"
    assert gh.requests[0] == "api.github.com/repos/acme/inv/commits/v1"


@pytest.mark.parametrize("url", ["https://evil.example/acme/inv", "http://github.com/acme/inv", "https://github.com.evil.io/a/b",
                                 "https://github.com/acme", "file:///etc/passwd", "https://github.com/acme/inv/tree/main", ""])
async def test_only_github_repo_urls_are_accepted(data_dir, monkeypatch, url):
    gh = FakeGithub(monkeypatch, {})
    with pytest.raises(InstallError, match="repository URL"):
        await installer.stage_github(url)
    assert gh.requests == []


async def test_github_errors_are_clear_and_leave_nothing(data_dir, monkeypatch):
    gh = FakeGithub(monkeypatch, {SHA1: tgz("1.0.0")})
    with pytest.raises(InstallError, match="ref 'nope' not found"):
        await installer.stage_github("https://github.com/acme/inv", "nope")
    with pytest.raises(InstallError, match="invalid ref"):
        await installer.stage_github("https://github.com/acme/inv", "../x")
    gh.status = 403
    with pytest.raises(InstallError, match="rate limited"):
        await installer.stage_github("https://github.com/acme/inv")
    gh.status = 404
    with pytest.raises(InstallError, match="public"):
        await installer.stage_github("https://github.com/acme/inv")
    assert_clean(data_dir)
    gh.status, gh.versions = 200, {}                                       # the tarball itself is missing
    with pytest.raises(InstallError, match="the archive not found"):
        await installer.stage_github("https://github.com/acme/inv")
    assert_clean(data_dir)


async def test_check_for_updates_compares_the_ref_with_the_recorded_commit_and_upgrade_installs_it(data_dir, session_factory, monkeypatch):
    gh = FakeGithub(monkeypatch, {SHA1: tgz("1.0.0"), SHA2: tgz("1.1.0")})
    staged = await installer.stage_github("https://github.com/acme/inv", "main")
    async with session_factory() as s:
        row = await installer.commit(s, staged.token, actor="a")
        row.status = "active"
        await s.commit()
        assert await installer.check_update(row) == {"update_available": False, "ref": "main", "current_commit": SHA1, "latest_commit": SHA1}
        gh.heads["main"] = SHA2
        info = await installer.check_update(row)
        assert info["update_available"] is True and info["latest_commit"] == SHA2
        up = await installer.stage_github(row.source_url, row.ref, row.subdir)
        row = await installer.commit(s, up.token, actor="a", expect_id="acme_inv")
        assert (row.version, row.commit_sha, row.previous_version) == ("1.1.0", SHA2, "1.0.0")


async def test_only_github_plugins_check_for_updates(data_dir, session_factory):
    row = await _install(session_factory)
    with pytest.raises(InstallError, match="only plugins installed from GitHub"):
        await installer.check_update(row)
