"""Tests for the base a fix pass hands its verify command (WORKBENCH_FIX_BASE)."""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.invoke  # noqa: E402
import fix.engine  # noqa: E402
import fix.suite  # noqa: E402
from config.workbench_config import FixConfig, WorkbenchConfig  # noqa: E402

from fix_engine_support import StubAdapter, _answer, _reads, head, landed  # noqa: F401
# autouse: stubs the worktree snapshots every pass reads; imported so it applies here
from fix_engine_support import snapshots  # noqa: F401


def _recorder_script(tmp_path: Path) -> str:
    script = tmp_path / "checks"
    out = tmp_path / "base.txt"
    script.write_text(f'#!/usr/bin/env bash\nprintf "%s" "${{WORKBENCH_FIX_BASE:-}}" > {out}\n')
    script.chmod(0o755)
    return str(script)


def test_the_suite_sees_the_base_it_was_given(tmp_path):
    fix.suite.run(tmp_path, _recorder_script(tmp_path), 30, base="abc123")
    assert (tmp_path / "base.txt").read_text() == "abc123"


def test_no_base_exports_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv(fix.suite.FIX_BASE_ENV, raising=False)
    fix.suite.run(tmp_path, _recorder_script(tmp_path), 30)
    assert (tmp_path / "base.txt").read_text() == ""


def test_the_engine_exports_the_head_the_pass_started_from(tmp_path, landed, head, snapshots):
    adapter = StubAdapter(tmp_path)
    adapter.config = WorkbenchConfig(fix=FixConfig(
        verify_command=_recorder_script(tmp_path), verify_timeout=30))
    snapshots.side_effect = _reads(set(), {"a.py"})
    with patch.object(agent.invoke, "run_fix", _answer(adapter)):
        fix.engine.run(adapter)
    # The `head` fixture pins git.client.head_sha to this value.
    assert (tmp_path / "base.txt").read_text() == "9999999"
