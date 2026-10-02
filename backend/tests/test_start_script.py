"""Static guard for the dev-server launcher (no PowerShell on CI): BIZ-147 — it must only ever kill what holds
:8001 and must fail loudly, never warn-and-continue, when the backend doesn't come up."""
import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / ".claude/skills/themis-start/scripts/start.ps1"


def _code() -> str:
    return "\n".join(l for l in SCRIPT.read_text(encoding="utf-8-sig").splitlines() if not l.lstrip().startswith("#"))


def test_never_kills_processes_by_name():
    assert not re.search(r"Get-Process\s+\S*python", _code(), re.IGNORECASE)
    assert not re.search(r"taskkill|Stop-Process\s+-Name|killall", _code(), re.IGNORECASE)


def test_kills_only_the_pids_listening_on_8001():
    code = _code()
    assert "(:8001).*LISTENING" in code
    assert code.count("Stop-Process") == 1 and "Stop-Process -Id" in code


def test_fails_loudly_when_port_not_freed_or_backend_never_answers():
    code = _code()
    assert "http://localhost:8001/api/v1/health" in code
    assert code.count("exit 1") == 2
    assert "may still be starting" not in code
