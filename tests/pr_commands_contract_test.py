"""`pr status` and `pr gc` contracts: a local dashboard and a GitHub-reading sweep."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr_commands  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402


def test_gc_writes_nothing_remotely():
    assert cli.pr_commands.GC_CONTRACT.remote_effects == ()


def test_gc_summary_reads_github_and_deletes_local_artifacts():
    text = cli.pr_commands.GC_CONTRACT.summary
    assert "Reads GitHub" in text
    assert "local artifacts" in text


def test_gc_exit_codes():
    assert [int(o.code) for o in cli.pr_commands.GC_CONTRACT.exits] == [0, 1, 2, 75]
    meaning = cli.pr_commands.GC_CONTRACT.outcome(ExitCode.TEMPFAIL).meaning
    assert "GitHub API budget" in meaning


def test_status_exit_codes():
    assert [int(o.code) for o in cli.pr_commands.STATUS_CONTRACT.exits] == [
        0, 1, 2]
