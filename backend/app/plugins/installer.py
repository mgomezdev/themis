"""Plugin installation pipeline (spec §3.11): get the archive (upload or a public GitHub repo at a resolved commit), extract
it safely into staging, validate `themis-plugin.toml`, dry-run the import in a subprocess, then commit it to
`<data>/plugins/<id>/<version>/` + an `installed_plugins` row. Nothing takes effect until an admin restarts Themis
(plugins/loader.py loads what is on disk); a failed install leaves nothing behind.

Plugins are trusted code (spec D17): this is validation and hygiene, not a sandbox."""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import IO, AsyncIterator
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config
from ..models import InstalledPlugin, PluginSchemaVersion
from ..services import audit
from ..version import get_version
from . import bundled_ids
from .manifest import ID_RE, PluginError
from .package import (TOML_NAME, VERSION_RE, PluginToml, check_min_themis, entry_file_exists, parse_toml, read_toml)

MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 80 * 1024 * 1024
MAX_FILES = 3000
DRYRUN_TIMEOUT_S = 30
INFO_FILE = ".themis-installed.json"           # per version dir: what that version shipped (read by rollback)

PENDING = ("pending_restart", "pending_removal")


class InstallError(PluginError):
    """The install was refused; the message is shown to the admin."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def plugins_dir() -> Path:
    return config.get_plugins_dir()


def staging_dir() -> Path:
    return plugins_dir() / ".staging"


def version_dir(plugin_id: str, version: str) -> Path:
    if not ID_RE.match(plugin_id) or not VERSION_RE.match(version):
        raise InstallError("invalid plugin id or version")
    return plugins_dir() / plugin_id / version


# --- staging ---------------------------------------------------------------------------------------------------------

@dataclass
class Staged:
    token: str
    toml: PluginToml
    archive_sha256: str
    source: str                      # upload | github
    source_url: str | None = None    # file name (upload) or repo url (github)
    ref: str | None = None
    subdir: str | None = None
    commit_sha: str | None = None
    migrations: tuple[int, ...] = ()

    @property
    def root(self) -> Path:
        return staging_dir() / self.token / "pkg"

    def preview(self) -> dict:
        t = self.toml
        return {"token": self.token, "id": t.id, "name": t.name, "version": t.version,
                "provides": [{"capability": c, "version": v} for c, v in t.provides],
                "requires": [{"capability": c, "min_version": v} for c, v in t.requires],
                "optional": [{"capability": c, "min_version": v} for c, v in t.optional], "defines": list(t.defines),
                "publisher": t.publisher, "description": t.description, "source": self.source,
                "source_url": self.source_url, "ref": self.ref, "commit_sha": self.commit_sha,
                "archive_sha256": self.archive_sha256, "min_themis": t.min_themis}


def _token_dir(token: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        raise InstallError("unknown or expired install token")
    return staging_dir() / token


def discard(token: str) -> None:
    shutil.rmtree(_token_dir(token), ignore_errors=True)


def _save_meta(s: Staged) -> None:
    meta = asdict(s)
    meta["toml"] = asdict(s.toml)
    (staging_dir() / s.token / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def load_staged(token: str) -> Staged:
    d = _token_dir(token)
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        t = meta.pop("toml")
        t["permissions"] = tuple(t.get("permissions", ()))
        meta["migrations"] = tuple(meta.get("migrations", ()))
        return Staged(toml=PluginToml(**t), **meta)
    except (OSError, ValueError, TypeError, KeyError) as e:
        raise InstallError("unknown or expired install token") from e


# --- receiving + safe extraction ---------------------------------------------------------------------------------------

async def receive_upload(chunks: AsyncIterator[bytes], dest: Path) -> str:
    """Stream the upload to `dest`, enforcing the size cap; returns the sha256."""
    h, size = hashlib.sha256(), 0
    with dest.open("wb") as f:
        async for chunk in chunks:
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES:
                raise InstallError(f"archive is larger than {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB")
            h.update(chunk)
            f.write(chunk)
    if size == 0:
        raise InstallError("the archive is empty")
    return h.hexdigest()


def _rel(name: str) -> PurePosixPath:
    """A member name as a safe relative path, or InstallError (absolute, `..`, backslash, drive letter, NUL)."""
    if "\x00" in name or "\\" in name:
        raise InstallError(f"unsafe path in archive: {name!r}")
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or (p.parts and re.match(r"^[A-Za-z]:", p.parts[0])):
        raise InstallError(f"unsafe path in archive: {name!r}")
    return p


class _Budget:
    def __init__(self) -> None:
        self.files = 0
        self.bytes = 0

    def file(self) -> None:
        self.files += 1
        if self.files > MAX_FILES:
            raise InstallError(f"archive has more than {MAX_FILES} files")

    def write(self, n: int) -> None:
        self.bytes += n
        if self.bytes > MAX_UNCOMPRESSED_BYTES:
            raise InstallError(f"archive expands to more than {MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB")


def _write(dest_root: Path, rel: PurePosixPath, src: IO[bytes], budget: _Budget) -> None:
    target = dest_root.joinpath(*rel.parts)
    if not target.resolve().is_relative_to(dest_root.resolve()):
        raise InstallError(f"unsafe path in archive: {str(rel)!r}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as out:
        while chunk := src.read(64 * 1024):           # counted as it is written: a lying header cannot bypass the cap
            budget.write(len(chunk))
            out.write(chunk)


def _extract_zip(path: Path, dest: Path) -> None:
    budget = _Budget()
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise InstallError(f"archive has more than {MAX_FILES} files")
        if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
            raise InstallError(f"archive expands to more than {MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB")
        for info in infos:
            rel = _rel(info.filename)
            mode = info.external_attr >> 16
            if info.flag_bits & 0x1:
                raise InstallError("encrypted archives are not supported")
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):      # no type bits at all = a plain file
                raise InstallError(f"links and special files are not allowed in a plugin archive: {info.filename!r}")
            if not rel.parts:
                continue
            budget.file()
            if info.is_dir():
                dest.joinpath(*rel.parts).mkdir(parents=True, exist_ok=True)
                continue
            with zf.open(info) as src:
                _write(dest, rel, src, budget)


class _Limited:
    """Read-through wrapper that refuses to hand out more than `limit` decompressed bytes: tar headers (GNU long names, PAX
    records, directory entries) are not file bytes, so the per-file budget alone cannot bound them."""
    def __init__(self, raw: IO[bytes], limit: int) -> None:
        self.raw, self.left = raw, limit

    def read(self, n: int = -1) -> bytes:
        data = self.raw.read(n)
        self.left -= len(data)
        if self.left < 0:
            raise InstallError(f"archive expands to more than {MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB")
        return data


def _extract_tar(path: Path, dest: Path) -> None:
    budget = _Budget()
    with gzip.open(path, "rb") as gz, tarfile.open(fileobj=_Limited(gz, MAX_UNCOMPRESSED_BYTES + 4 * 1024 * 1024), mode="r|") as tf:
        for m in tf:                                     # streamed, member by member; never `extractall`
            rel = _rel(m.name)
            if m.issym() or m.islnk() or m.isdev() or m.isfifo():
                raise InstallError(f"links and special files are not allowed in a plugin archive: {m.name!r}")
            if not rel.parts:
                continue
            if m.isdir():
                budget.file()                            # entries count too: a tgz of millions of empty dirs is a bomb
                dest.joinpath(*rel.parts).mkdir(parents=True, exist_ok=True)
            elif m.isreg():
                budget.file()
                src = tf.extractfile(m)
                if src is None:
                    raise InstallError(f"unreadable archive member: {m.name!r}")
                _write(dest, rel, src, budget)
            else:
                raise InstallError(f"unsupported archive member: {m.name!r}")


def extract_archive(archive: Path, dest: Path) -> None:
    """Extract a zip / tar.gz / tgz (detected by content, not by name) into `dest` under the safety limits."""
    dest.mkdir(parents=True, exist_ok=True)
    with archive.open("rb") as f:
        magic = f.read(4)
    try:
        if magic[:2] == b"PK":
            _extract_zip(archive, dest)
        elif magic[:2] == b"\x1f\x8b":
            _extract_tar(archive, dest)
        else:
            raise InstallError("not a .zip, .tar.gz or .tgz archive")
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError, gzip.BadGzipFile) as e:
        if isinstance(e, InstallError):
            raise
        raise InstallError(f"the archive is corrupt or unreadable: {e}") from e


def locate_root(extracted: Path, subdir: str | None) -> Path:
    """The package root: the extraction dir, or its single top-level folder (how GitHub and `zip folder/` pack things),
    then the optional subdirectory (monorepos)."""
    base = extracted
    entries = list(extracted.iterdir())
    if len(entries) == 1 and entries[0].is_dir() and not (extracted / TOML_NAME).exists():
        base = entries[0]
    if subdir:
        rel = _rel(subdir.strip("/"))
        base = base.joinpath(*rel.parts)
        if not base.resolve().is_relative_to(extracted.resolve()) or not base.is_dir():
            raise InstallError(f"subdirectory {subdir!r} not found in the archive")
    return base


# --- validation ----------------------------------------------------------------------------------------------------

_DRYRUN = r'''
import importlib, json, sys
root, vendor, module, attr = sys.argv[1:5]
sys.path.append(root)
sys.path.append(vendor)
m = getattr(importlib.import_module(module), attr)
from app.plugins.manifest import PluginManifest
if not isinstance(m, PluginManifest):
    raise SystemExit("entry is not a PluginManifest")
print("@@themis-manifest@@" + json.dumps({"id": m.id, "version": m.version, "host_api": m.host_api,
      "provides": sorted([c, p.version] for c, p in m.provides.items()),
      "requires": sorted([r.capability, r.min_version] for r in m.requires),
      "optional": sorted([r.capability, r.min_version] for r in m.optional),
      "defines": sorted(d.id for d in m.defines),
      "migrations": sorted(x.version for x in m.migrations)}))
'''


def _taken(top: str, plugin_id: str, base: Path | None = None) -> bool:
    """True when a top-level module of that name already resolves in this process outside the plugin's own install."""
    spec = importlib.util.find_spec(top)
    if spec is None:
        return False
    origin = spec.origin or (list(spec.submodule_search_locations or [""])[0])
    own = (base or plugins_dir()) / plugin_id
    try:
        return not Path(origin).resolve().is_relative_to(own.resolve())
    except (OSError, ValueError):
        return True


def dry_run(root: Path, t: PluginToml) -> dict:
    """Import the entry in a throwaway subprocess with the plugin's `vendor/` after Themis's own path: import errors,
    missing deps and a MANIFEST that disagrees with the toml fail the install without touching this process."""
    scratch = root.parent / "scratch"
    scratch.mkdir(exist_ok=True)
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
           "THEMIS_DATA_DIR": str(scratch), "HOME": str(scratch), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = subprocess.run([sys.executable, "-P", "-c", _DRYRUN, str(root), str(root / "vendor"), t.module, t.attr],
                              capture_output=True, text=True, timeout=DRYRUN_TIMEOUT_S, env=env, cwd=scratch)
    except subprocess.TimeoutExpired as e:
        raise InstallError(f"importing the plugin took longer than {DRYRUN_TIMEOUT_S}s") from e
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        raise InstallError("the plugin failed to import: " + " | ".join(tail)[:600])
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("@@themis-manifest@@")), None)
    if line is None:
        raise InstallError("the plugin did not export a MANIFEST")
    info = json.loads(line.split("@@themis-manifest@@", 1)[1])
    for k in ("id", "version", "host_api"):
        if info[k] != getattr(t, k):
            raise InstallError(f"{TOML_NAME} {k} {getattr(t, k)!r} does not match MANIFEST {k} {info[k]!r}")
    for k in ("provides", "requires", "optional"):
        if sorted(list(x) for x in getattr(t, k)) != info[k]:
            raise InstallError(f"{TOML_NAME} {k} {sorted(list(x) for x in getattr(t, k))!r} does not match MANIFEST {k} {info[k]!r}")
    if sorted(t.defines) != info["defines"]:
        raise InstallError(f"{TOML_NAME} defines {sorted(t.defines)!r} does not match MANIFEST defines {info['defines']!r}")
    return info


def validate_package(root: Path) -> PluginToml:
    """Static checks only. NOTHING from the package is imported or run here: previews (and update checks) must not execute
    code the admin has not yet agreed to trust. The import happens in `commit`, after the explicit confirmation."""
    try:
        t = read_toml(root, reserved_ids=bundled_ids())
        if not entry_file_exists(root, t):
            raise InstallError(f"entry module {t.module!r} not found in the package")
        if _taken(t.top_package, t.id):
            raise InstallError(f"top-level package name {t.top_package!r} is already used by Themis or another plugin")
        check_min_themis(t, get_version())
    except InstallError:
        raise
    except PluginError as e:                              # toml validation errors are the admin's to read: same type
        raise InstallError(str(e)) from e
    return t


async def _stage(archive: Path, token: str, sha256: str, **source) -> Staged:
    tdir = staging_dir() / token
    try:
        extracted = tdir / "extracted"
        await asyncio.to_thread(extract_archive, archive, extracted)
        root = locate_root(extracted, source.get("subdir"))
        shutil.move(str(root), str(tdir / "pkg"))
        shutil.rmtree(extracted, ignore_errors=True)
        archive.unlink(missing_ok=True)
        t = await asyncio.to_thread(validate_package, tdir / "pkg")
        staged = Staged(token=token, toml=t, archive_sha256=sha256, **source)
        _save_meta(staged)
        return staged
    except BaseException:
        shutil.rmtree(tdir, ignore_errors=True)         # a failed install leaves nothing on disk
        raise


STAGING_TTL_S = 3600


def _sweep_staging() -> None:
    """Previews nobody confirmed or discarded (closed tab) expire after an hour."""
    base = staging_dir()
    cutoff = time.time() - STAGING_TTL_S
    for d in base.iterdir() if base.is_dir() else []:
        try:
            if d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def new_token_dir() -> tuple[str, Path]:
    _sweep_staging()
    token = uuid.uuid4().hex
    d = staging_dir() / token
    d.mkdir(parents=True, exist_ok=False)
    return token, d


async def stage_upload(chunks: AsyncIterator[bytes], filename: str) -> Staged:
    token, d = new_token_dir()
    try:
        sha = await receive_upload(chunks, d / "archive")
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return await _stage(d / "archive", token, sha, source="upload", source_url=(filename or "upload")[:200])


# --- GitHub (public repositories only; BIZ-199 tracks private ones) -------------------------------------------------

_REPO_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100}?)(?:\.git)?/?$")
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def parse_repo_url(url: str) -> tuple[str, str]:
    m = _REPO_RE.match((url or "").strip())
    if not m:
        raise InstallError("repository URL must look like https://github.com/<owner>/<repo>")
    return m.group(1), m.group(2)


def _client() -> httpx.AsyncClient:                     # tests substitute a client with a mock transport
    return httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers={"User-Agent": "themis-plugin-installer"})


def _gh_error(r: httpx.Response, what: str) -> InstallError:
    if r.status_code == 404:
        return InstallError(f"{what} not found (only public repositories can be installed)")
    if r.status_code in (403, 429):
        return InstallError("GitHub refused the request (rate limited?); try again later")
    return InstallError(f"GitHub returned {r.status_code} for {what}")


async def resolve_ref(owner: str, repo: str, ref: str | None) -> tuple[str, str]:
    """(ref used, commit sha). No ref = the repository's default branch."""
    if ref is not None and not (_REF_RE.match(ref) and ".." not in ref and not ref.startswith(("-", "/"))):
        raise InstallError("invalid ref")
    api = f"https://api.github.com/repos/{owner}/{repo}"
    hdr = {"Accept": "application/vnd.github+json"}
    try:
        async with _client() as c:
            if not ref:
                r = await c.get(api, headers=hdr)
                if r.status_code != 200:
                    raise _gh_error(r, "repository")
                ref = r.json()["default_branch"]
            r = await c.get(f"{api}/commits/{quote(ref, safe='/')}", headers=hdr)
            if r.status_code != 200:
                raise _gh_error(r, f"ref {ref!r}")
            sha = r.json()["sha"]
    except httpx.HTTPError as e:
        raise InstallError(f"could not reach GitHub: {e}") from e
    if not _SHA_RE.match(sha):
        raise InstallError("GitHub returned an unexpected commit id")
    return ref, sha


async def _download(owner: str, repo: str, sha: str, dest: Path) -> str:
    async def chunks() -> AsyncIterator[bytes]:
        async with _client() as c:
            async with c.stream("GET", f"https://codeload.github.com/{owner}/{repo}/tar.gz/{sha}") as r:
                if r.status_code != 200:
                    await r.aread()
                    raise _gh_error(r, "the archive")
                async for part in r.aiter_bytes():
                    yield part
    try:
        return await receive_upload(chunks(), dest)
    except httpx.HTTPError as e:
        raise InstallError(f"could not download from GitHub: {e}") from e


async def stage_github(repo_url: str, ref: str | None = None, subdir: str | None = None) -> Staged:
    owner, repo = parse_repo_url(repo_url)
    if subdir:
        _rel(subdir.strip("/"))
    used_ref, sha = await resolve_ref(owner, repo, ref)
    token, d = new_token_dir()
    try:
        digest = await _download(owner, repo, sha, d / "archive")      # the resolved commit, never the moving ref
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return await _stage(d / "archive", token, digest, source="github", source_url=f"https://github.com/{owner}/{repo}",
                        ref=used_ref, subdir=subdir or None, commit_sha=sha)


async def check_update(row: InstalledPlugin) -> dict:
    if row.source != "github" or not row.source_url:
        raise InstallError("only plugins installed from GitHub can be checked for updates")
    owner, repo = parse_repo_url(row.source_url)
    ref, sha = await resolve_ref(owner, repo, row.ref)
    return {"update_available": sha != row.commit_sha, "ref": ref, "current_commit": row.commit_sha, "latest_commit": sha}


# --- commit / rollback / uninstall -----------------------------------------------------------------------------------

async def get_row(session: AsyncSession, plugin_id: str) -> InstalledPlugin:
    row = await session.get(InstalledPlugin, plugin_id)
    if row is None:
        raise InstallError(f"{plugin_id!r} is not an installed plugin (bundled plugins can only be disabled)")
    return row


def _prune(plugin_id: str, keep: set[str]) -> None:
    base = plugins_dir() / plugin_id
    for d in base.iterdir() if base.is_dir() else []:
        if d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)


_commit_lock = asyncio.Lock()          # one commit at a time: two confirmations of one token cannot race on the move


async def commit(session: AsyncSession, token: str, *, actor: str, expect_id: str | None = None) -> InstalledPlugin:
    """Import-check, then move a staged package into place and record it. New id = install; existing id = upgrade (the
    version that is running stays on disk as `previous_version`). The plugin only loads at the next restart."""
    async with _commit_lock:
        staged = load_staged(token)
        t = staged.toml
        try:
            if expect_id is not None and t.id != expect_id:
                raise InstallError(f"the package is plugin {t.id!r}, not {expect_id!r}")
            row = await session.get(InstalledPlugin, t.id)
            if row is not None and row.status == "pending_removal":
                raise InstallError(f"{t.id!r} is waiting to be uninstalled; restart Themis first")
            target = version_dir(t.id, t.version)
            if target.exists():
                raise InstallError(f"version {t.version} of {t.id!r} is already installed; bump the version")
            info = await asyncio.to_thread(dry_run, staged.root, t)            # first time any of its code runs: after consent
            staged.migrations = tuple(info["migrations"])
        except BaseException:
            shutil.rmtree(staging_dir() / token, ignore_errors=True)            # a refused package is not kept
            raise
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            (staged.root / INFO_FILE).write_text(json.dumps({"migrations": list(staged.migrations)}), encoding="utf-8")
            await asyncio.to_thread(shutil.move, str(staged.root), str(target))
            action = "plugin.install" if row is None else "plugin.upgrade"
            # The version that is actually RUNNING is what a rollback must return to and what must not be deleted. A row
            # still `pending_restart` has not run yet: the running one is whatever it recorded as `previous_version`.
            if row is None:
                previous = None
            elif row.status == "pending_restart":
                previous = row.previous_version
            else:
                previous = row.version
            if row is None:
                row = InstalledPlugin(plugin_id=t.id)
                session.add(row)
            row.version, row.name, row.publisher = t.version, t.name, t.publisher
            row.source, row.source_url, row.ref, row.subdir = staged.source, staged.source_url, staged.ref, staged.subdir
            row.commit_sha, row.archive_sha256, row.installed_at = staged.commit_sha, staged.archive_sha256, _now()
            row.status, row.error, row.previous_version = "pending_restart", None, previous
            await audit.record(session, actor, action, t.id, {
                "version": t.version, "previous_version": previous, "source": staged.source, "source_url": staged.source_url,
                "commit_sha": staged.commit_sha, "archive_sha256": staged.archive_sha256})
            await session.commit()
        except BaseException as e:
            await session.rollback()
            shutil.rmtree(target, ignore_errors=True)                           # nothing half-installed stays behind
            try:
                target.parent.rmdir()                                           # the id's folder too, unless it holds a live version
            except OSError:
                pass
            if isinstance(e, OSError):
                raise InstallError(f"could not install the package: {e}") from e
            raise
        finally:
            shutil.rmtree(staging_dir() / token, ignore_errors=True)
        _prune(t.id, {t.version} | ({previous} if previous else set()))
        return row


async def rollback(session: AsyncSession, plugin_id: str, *, actor: str) -> InstalledPlugin:
    row = await get_row(session, plugin_id)
    prev = row.previous_version
    if row.status == "pending_removal":
        raise InstallError("this plugin is waiting to be uninstalled")
    if not prev or not version_dir(plugin_id, prev).is_dir():
        raise InstallError("there is no previous version to roll back to")
    try:
        shipped = set(json.loads((version_dir(plugin_id, prev) / INFO_FILE).read_text(encoding="utf-8"))["migrations"])
    except (OSError, ValueError, KeyError):
        shipped = set()
    applied = {r for (r,) in (await session.execute(
        select(PluginSchemaVersion.version).where(PluginSchemaVersion.plugin_id == plugin_id))).all()}
    ahead = sorted(applied - shipped)
    if ahead:
        raise InstallError(f"cannot roll back to {prev}: the database already has migration(s) {ahead} that {prev} "
                           f"does not ship (a rollback across a migration is refused)")
    left = row.version
    row.version, row.previous_version, row.status, row.error = prev, left, "pending_restart", None
    row.commit_sha = None if row.source == "github" else row.commit_sha     # the recorded commit belonged to `left`
    await audit.record(session, actor, "plugin.rollback", plugin_id, {"from": left, "to": prev})
    await session.commit()
    return row


async def mark_uninstall(session: AsyncSession, plugin_id: str, *, actor: str, removed_data: bool) -> InstalledPlugin:
    row = await get_row(session, plugin_id)
    row.status, row.error = "pending_removal", None
    await audit.record(session, actor, "plugin.uninstall", plugin_id, {"version": row.version, "removed_data": removed_data})
    await session.commit()
    return row
