"""Build identity of the running server: package version + the git commit it was built from.

Docker/CI bake the commit in via THEMIS_GIT_SHA (Dockerfile ARG GIT_SHA). In a local checkout there is no
build step, so fall back to asking git once; "unknown" when neither is available.
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from importlib import metadata
from pathlib import Path


def get_version() -> str:
    try:
        return metadata.version("themis")
    except metadata.PackageNotFoundError:
        return "unknown"


@lru_cache(maxsize=1)
def get_git_sha() -> str:
    env = os.environ.get("THEMIS_GIT_SHA", "").strip()
    if env:
        return env
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, timeout=2, check=True,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"
