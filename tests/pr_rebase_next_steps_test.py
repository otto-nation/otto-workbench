"""`pr rebase` prints the contract's Next: block on stderr after exit 3."""

import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.locks  # noqa: E402
import cli.pr_rebase  # noqa: E402
import pr.context  # noqa: E402
import rebase.commands  # noqa: E402
import rebase.inspect  # noqa: E402

from conftest import make_ctx  # noqa: E402


@pytest.fixture
def ran(monkeypatch, tmp_path):
    ctx = make_ctx(branch="isaac/feat/x", worktree_root=tmp_path)

    def run(rc, paused):
        monkeypatch.setattr(pr.context, "resolve", lambda **kw: ctx)
        monkeypatch.setattr(cli.locks, "claim_declared", lambda *a, **kw: None)
        monkeypatch.setattr(rebase.commands, "_run", lambda *a: rc)
        monkeypatch.setattr(rebase.inspect, "rebase_in_progress", lambda cwd: paused)
        return cli.pr_rebase.main([])
    return run


def test_a_paused_stop_prints_how_to_finish(ran, capsys):
    assert ran(3, True) == 3
    _, err = capsys.readouterr()
    assert "Next:\n" in err
    assert "rebase --continue" in err
    assert "ls-remote origin refs/heads/isaac/feat/x" in err
    assert "pr rebase --push-only --expect" in err


def test_a_clean_abort_prints_how_to_retry(ran, capsys):
    assert ran(3, False) == 3
    err = capsys.readouterr().err
    assert "pr rebase --fix --branch isaac/feat/x" in err
    assert "rebase --continue" not in err


def test_success_prints_no_next_block(ran, capsys):
    assert ran(0, False) == 0
    assert "Next:" not in capsys.readouterr().err
