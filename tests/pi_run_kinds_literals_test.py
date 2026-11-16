"""Kind and env-var spellings live only in run-dir.ts.

Reads every .ts file in ai/pi/extensions/run-session/ and
ai/pi/extensions/session-lock/ (sorted), strips // line comments and /* */
block comments, and asserts no string literal matches batch/target or names
WORKBENCH_RUN_DIR / WORKBENCH_TARGET_DIR / WORKBENCH_SESSION_ID. Only
ai/pi/extensions/_shared/run-dir.ts may spell them.

Revert: put `"batch"` back in a literal in wire.ts and it fails.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXT = REPO_ROOT / "ai" / "pi" / "extensions"
DIRS = (EXT / "run-session", EXT / "session-lock")
FORBIDDEN_ENV = ("WORKBENCH_RUN_DIR", "WORKBENCH_TARGET_DIR", "WORKBENCH_SESSION_ID")
KIND_RE = re.compile(r"\b(batch|target)\b")
STRING_RE = re.compile(r""""(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`""")


def _strip_comments(src: str) -> str:
    # No `//` inside strings in these files (none of the form "http://").
    no_block = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", no_block)


def _literals(src: str):
    for match in STRING_RE.finditer(src):
        yield match.group(0)[1:-1]


def _ts_files():
    return sorted(p for d in DIRS for p in d.glob("*.ts"))


def test_run_session_and_session_lock_literals_do_not_spell_kinds_or_env():
    offenders = []
    for path in _ts_files():
        body = _strip_comments(path.read_text())
        rel = path.relative_to(REPO_ROOT)
        for lit in _literals(body):
            if KIND_RE.search(lit) or any(name in lit for name in FORBIDDEN_ENV):
                offenders.append(f"{rel}: {lit!r}")
    assert offenders == []
