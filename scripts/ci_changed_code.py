"""CI path filter (see the `changes` job in .github/workflows/ci.yml).

Reads changed file paths on stdin, one per line, and prints `true` when any of them can affect what CI tests, or
`false` when every one is documentation. An empty list prints `true`: no evidence is not evidence of docs-only.

Documentation means a Markdown file anywhere, anything else under docs/, or LICENSE -- EXCEPT the documentation the test
suite itself reads (TESTED_DOCS): a wrong row in the plugin test matrix or a deleted reference 3MF fails a backend test,
so a change there must run the pipeline. `backend/tests/test_ci_docs_filter.py` fails when a test starts reading a
docs path that is not listed. Everything else is code, including .claude/ (hooks, and a skill script with its own
test), openapi.json, contracts/, the workflow files and this script.
"""
import sys

DOC_DIRS = ("docs/",)
DOC_FILES = ("LICENSE",)
# Paths (a file, or a directory ending in "/") under docs/ that tests open.
TESTED_DOCS = (
    "docs/reference 3mfs/",
    "docs/plugin-test-matrix.md",
    "docs/plugins.md",
    "docs/provider-interfaces.md",
)


def is_doc(path: str) -> bool:
    if path.startswith(".claude/"):
        return False
    if any(path == t or (t.endswith("/") and path.startswith(t)) for t in TESTED_DOCS):
        return False
    return path.startswith(DOC_DIRS) or path.endswith(".md") or path in DOC_FILES


def changed_code(paths: list[str]) -> bool:
    paths = [p.strip() for p in paths if p.strip()]
    return not paths or any(not is_doc(p) for p in paths)


if __name__ == "__main__":
    print("true" if changed_code(sys.stdin.read().splitlines()) else "false")
