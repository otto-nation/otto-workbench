"""`--track` on the review entry: parser refusals and the no-agent short-circuit."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import make_ctx
from review.record import Disposition
from review_entry_support import cr, reviews_dir
from review_record_support import finding, record, seed_record

import agent.backend
import cli.locks
import cli.needs
import cli.pr
import cli.review_modes
import core.run_lock
import pr.context
import review.issue
import review.run

HEAD = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TARGET = core.run_lock.TARGET


@pytest.mark.parametrize("argv,needle", [
    (["--track", "M1"], "--track requires --self"),
    (["--self", "--track", "M1", "--fix"], "cannot run with --fix"),
    (["--self", "--track", "M1", "--force"], "cannot run with --force"),
    (["--self", "--track", "M1", "--recover"], "cannot run with --recover"),
])
def test_track_is_a_parser_error_with_run_shaping_flags(
    cr, reviews_dir, monkeypatch, argv, needle, capsys,
):
    """Revert: drop the `_track_error` check — each of these would start a review."""
    monkeypatch.setattr(cr, "_run_self_review", MagicMock())
    monkeypatch.setattr(cr, "_run_review", MagicMock())
    monkeypatch.setattr(cr, "_run_file_tracked", MagicMock(return_value=0))
    assert cr.main(argv) == 1
    assert needle in capsys.readouterr().err
    cr._run_file_tracked.assert_not_called()
    cr._run_self_review.assert_not_called()


def test_track_with_fix_is_visible_on_the_parser(cr, capsys):
    """`--track` is on the review parser, and `--track --fix` is refused after parse."""
    parsed = cr.build_parser().parse_args(["--self", "--track", "M1"])
    assert parsed.track == ["M1"]
    assert parsed.self_review is True


def test_track_does_not_run_a_review(cr, reviews_dir, monkeypatch):
    """No agent, no self-review flow. Revert: drop the short-circuit before `_run_self_review`."""
    seed_record(
        reviews_dir, name="repo-self-feat-test",
        rec=record(finding("M1", "boom", disposition=Disposition.UNREACHED)),
        head_sha=HEAD,
    )
    ctx = make_ctx(repo="owner/repo", branch="feat/test", pr_number=None, head_sha=HEAD)
    monkeypatch.setattr(pr.context, "resolve_at", lambda *a, **k: ctx)
    monkeypatch.setattr(cli.locks, "claim_declared", lambda *a, **k: None)
    monkeypatch.setattr(cr, "_run_self_review", MagicMock(side_effect=AssertionError("review")))
    monkeypatch.setattr(review.run, "run_self_review", MagicMock(side_effect=AssertionError("run")))
    monkeypatch.setattr(agent.backend, "preflight", MagicMock(side_effect=AssertionError("backend")))

    assert cr.main(["--self", "--track", "M1"]) == 0
    agent.backend.preflight.assert_not_called()


def test_track_without_post_does_not_create_an_issue(cr, reviews_dir, monkeypatch):
    """Revert: call `create_issue` on the draft path."""
    seed_record(
        reviews_dir, name="repo-self-feat-test",
        rec=record(finding("M1", "boom", disposition=Disposition.UNREACHED)),
        head_sha=HEAD,
    )
    ctx = make_ctx(repo="owner/repo", branch="feat/test", pr_number=None, head_sha=HEAD)
    monkeypatch.setattr(pr.context, "resolve_at", lambda *a, **k: ctx)
    monkeypatch.setattr(cli.locks, "claim_declared", lambda *a, **k: None)
    monkeypatch.setattr(
        review.issue, "create_issue",
        MagicMock(side_effect=AssertionError("create_issue")),
    )
    assert cr.main(["--self", "--track", "M1"]) == 0


def test_track_with_post_is_not_the_post_mode():
    """`--post` beside `--track` opens the gate; it must not route to review-post."""
    assert cli.review_modes.flags_given(["--self", "--track", "M1", "--post"]) == []


def test_cmd_review_refuses_track_beside_a_mode(capsys):
    rc = cli.pr.cmd_review(
        ["--self", "--track", "M1", "--summary"], make_ctx(), bin_dir=Path("/bin"),
    )
    assert rc == 1
    assert "--summary" in capsys.readouterr().err


def test_self_track_declares_target_only():
    need = cli.needs.review_need(
        ["--self", "--track", "M1"], cli.review_modes.MODES,
    )
    assert need == cli.needs.REVIEW_SELF_TRACK_NEED
    assert need.locks == frozenset({TARGET})
    assert need.update is False
