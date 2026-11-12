"""Meta-test: every test checks something. A test with no assertion passes whatever the code does ("it didn't crash"),
which is the gap CLAUDE.md § Testing warns about. This scans the suite's source and fails on a test function that has
no `assert`, no `pytest.raises`/`warns`/`fail`, and no assert-style call (`mock.assert_called_once()`, `assert_golden`,
`wait_until`, a helper named `assert_*`/`expect_*`/`check_*`).

It cannot judge whether an assertion is a *good* one; that stays with review (docs/agent/backend-review.md). No exceptions
today: one goes in ALLOWLIST with its reason, and a stale entry fails."""
import ast
from pathlib import Path

TESTS = Path(__file__).parent
# Call names (the last dotted part) that count as a check.
CHECK_PREFIXES = ("assert", "expect", "check", "verify")
CHECK_NAMES = {"raises", "warns", "fail", "wait_until", "deprecated_call"}

ALLOWLIST: dict[str, str] = {}


def _checks(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in CHECK_NAMES or name.lstrip("_").startswith(CHECK_PREFIXES):
                return True
    return False


def _test_functions(tree: ast.Module):
    """Collected tests only: module-level `test_*` functions and `test_*` methods of module-level `Test*` classes
    (a `def test_connection` on a fake provider inside a test body is not a test)."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            yield node
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"):
                    yield item


def assertion_free(source: str) -> list[str]:
    return [fn.name for fn in _test_functions(ast.parse(source)) if not _checks(fn)]


def _scan() -> tuple[list[str], int]:
    found, files = [], 0
    for path in sorted(TESTS.rglob("test_*.py")):
        files += 1
        rel = path.relative_to(TESTS).as_posix()
        found += [f"{rel}::{name}" for name in assertion_free(path.read_text(encoding="utf-8"))]
    return found, files


def test_every_test_asserts_something():
    found, _ = _scan()
    assert [f for f in found if f not in ALLOWLIST] == []


def test_every_allowlist_entry_is_still_needed():
    found, _ = _scan()
    assert [entry for entry in ALLOWLIST if entry not in found] == []


def test_the_scan_covers_the_whole_suite():
    _, files = _scan()
    assert files > 250
    assert (TESTS / "api" / "test_printers_api.py").exists()


def test_it_can_fail_and_does_not_flag_real_checks():
    source = '''
async def test_nothing(client):
    await client.post("/x")

def test_asserts():
    assert 1 == 1

def test_raises():
    with pytest.raises(ValueError):
        int("x")

def test_mock(m):
    m.assert_called_once()

async def test_waits():
    await wait_until(lambda: True)

def test_helper():
    _assert_shape({})

def helper_not_a_test():
    pass

class TestGroup:
    def test_method_without_a_check(self):
        print("ran")

def test_outer():
    class Fake:
        async def test_connection(self): return {}
    assert Fake
'''
    assert assertion_free(source) == ["test_nothing", "test_method_without_a_check"]
