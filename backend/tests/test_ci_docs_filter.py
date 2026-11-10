"""The CI path filter (scripts/ci_changed_code.py) lets a documentation-only pull request skip the test, image and e2e
jobs. That is only safe while no skipped job would have failed, so: the classification is pinned here, and any docs
path a test opens must be listed in the filter's TESTED_DOCS (a change to it then runs the pipeline)."""
import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("ci_changed_code", REPO / "scripts" / "ci_changed_code.py")
ci = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci)


@pytest.mark.parametrize("paths", [
    ["docs/user-guide.md"],
    ["README.md", "CONTRIBUTING.md", "CLAUDE.md", "docs/agent/backend.md"],
    ["docs/superpowers/plans/2026-11-01-something.md", "docs/design/themis/project/a.png", "LICENSE"],
    ["backend/README.md"],
])
def test_documentation_only_changes_skip_the_pipeline(paths):
    assert ci.changed_code(paths) is False


@pytest.mark.parametrize("paths", [
    ["backend/app/main.py"],
    ["docs/user-guide.md", "frontend/src/App.tsx"],          # one code file is enough
    ["openapi.json"], ["contracts/response-keys.json"], ["Dockerfile"], ["docker-compose.yml"],
    [".github/workflows/ci.yml"], ["scripts/ci_changed_code.py"],
    [".claude/hooks/gate-pr-review.js"], [".claude/skills/themis-start/scripts/start.ps1"],
    ["docs/plugin-test-matrix.md"], ["docs/plugins.md"], ["docs/provider-interfaces.md"],
    ["docs/reference 3mfs/anything.3mf"],                   # documentation the test suite reads
    ["mock/Dockerfile"], ["backend/pyproject.toml"], ["frontend/package-lock.json"],
    [],                                                     # nothing listed: run everything rather than assume
])
def test_anything_ci_can_test_runs_the_pipeline(paths):
    assert ci.changed_code(paths) is True


def _docs_paths_read_by_tests() -> set[str]:
    """`... / "docs" / "name"` and `"docs/name"` path literals in the Python suites."""
    found: set[str] = set()
    for root in (REPO / "backend" / "tests", REPO / "mcp-server"):
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            found |= {f"docs/{m}" for m in re.findall(r'"docs"\s*/\s*"([^"]+)"', text)}
            found |= set(re.findall(r'"(docs/[^"\n]+\.[a-z0-9]+)"', text))
    return found


def test_every_docs_path_a_test_reads_makes_the_pipeline_run():
    read = _docs_paths_read_by_tests()
    assert {"docs/plugin-test-matrix.md", "docs/reference 3mfs"} <= read               # the scan still finds the known ones
    unlisted = sorted(p for p in read if ci.is_doc(p + "/x" if (REPO / p).is_dir() else p))
    assert unlisted == [], f"add to TESTED_DOCS in scripts/ci_changed_code.py: {unlisted}"


def test_every_listed_path_exists():
    assert [t for t in ci.TESTED_DOCS if not (REPO / t).exists()] == []
