"""docs/plugin-test-matrix.md must stay true (BIZ-304): every row names tests that exist and run, and every area has a row."""
import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
DOC = REPO / "docs" / "plugin-test-matrix.md"
AREAS = {"manifest-host-api", "dependencies", "failure-recovery", "enable-select", "ui-contributions", "data-retention", "events",
         "workflows"}


def _rows() -> list[tuple[str, str, list[str]]]:
    out = []
    for line in DOC.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| (M\d+) \| ([a-z-]+) \|.*\| (`.*`) \|$", line)
        if m:
            out.append((m.group(1), m.group(2), re.findall(r"`([^`]+::[^`]+)`", m.group(3))))
    return out


def _resolve(ref: str) -> Path:
    path, _, _ = ref.partition("::")
    return REPO / path if path.startswith("frontend/") else BACKEND / path


def _python_test(path: Path, name: str):
    return next((n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)


def test_the_matrix_has_unique_rows_each_with_tests_and_covers_every_area():
    rows = _rows()
    assert len(rows) >= 20
    assert len({r[0] for r in rows}) == len(rows)
    assert {r[1] for r in rows} == AREAS
    assert [r[0] for r in rows if not r[2]] == []


@pytest.mark.parametrize("row_id, ref", [(rid, ref) for rid, _, refs in _rows() for ref in refs], ids=lambda v: str(v)[:60])
def test_every_referenced_test_exists_and_is_not_skipped(row_id, ref):
    path, _, name = ref.partition("::")
    file = _resolve(ref)
    assert file.is_file(), f"{row_id}: {path} does not exist"
    if file.suffix == ".py":
        node = _python_test(file, name)
        assert node is not None, f"{row_id}: {name} is not defined in {path}"
        assert not any("skip" in ast.unparse(d) or "xfail" in ast.unparse(d) for d in node.decorator_list), f"{row_id}: {name} is skipped"
    else:
        assert name in file.read_text(encoding="utf-8"), f"{row_id}: no test titled like {name!r} in {path}"


def test_the_matrix_tests_need_no_hardware_or_external_service():
    """The suites the matrix points at use fakes only: none is marked as hardware-dependent or runs a protocol verification."""
    files = {_resolve(ref) for _, _, refs in _rows() for ref in refs if ref.partition("::")[0].endswith(".py")}
    assert len(files) >= 15
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert "pytest.mark.hardware" not in text and "protocol_verification" not in text, f.name
