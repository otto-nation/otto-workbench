"""Unit tests for review.preflight — importable without executing claude-review."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review import preflight as review_preflight

from conftest import (
    supersession_context, supersession_evidence, supersession_verdict,
)


# ── check_pending_review ──────────────────────────────────────────────────────


def test_check_pending_review_force_skips_github(monkeypatch):
    login = MagicMock(side_effect=AssertionError("login called"))
    monkeypatch.setattr(review_preflight.gh_client, "login", login)
    review_preflight.check_pending_review("owner/repo", "1", force=True)
    assert not login.called


def test_check_pending_review_no_pending_is_silent(monkeypatch):
    monkeypatch.setattr(review_preflight.gh_client, "login", lambda: "me")
    monkeypatch.setattr(review_preflight.gh_client, "api_json", lambda *a, **kw: {})
    api = MagicMock(side_effect=AssertionError("api called"))
    monkeypatch.setattr(review_preflight.gh_client, "api", api)
    review_preflight.check_pending_review("owner/repo", "1", force=False)
    assert not api.called


def test_check_pending_review_non_dict_is_no_pending(monkeypatch):
    """`first // empty` leaves gh stdout empty; api_json's default is not a dict."""
    monkeypatch.setattr(review_preflight.gh_client, "login", lambda: "me")
    monkeypatch.setattr(review_preflight.gh_client, "api_json", lambda *a, **kw: [])
    api = MagicMock(side_effect=AssertionError("api called"))
    monkeypatch.setattr(review_preflight.gh_client, "api", api)
    review_preflight.check_pending_review("owner/repo", "1", force=False)
    assert not api.called


def test_check_pending_review_decline_keeps_it(monkeypatch):
    monkeypatch.setattr(review_preflight.gh_client, "login", lambda: "me")
    monkeypatch.setattr(
        review_preflight.gh_client, "api_json", lambda *a, **kw: {"id": 99},
    )
    api = MagicMock(return_value=SimpleNamespace(ok=True, stdout="3\n"))
    monkeypatch.setattr(review_preflight.gh_client, "api", api)
    monkeypatch.setattr(review_preflight.prompt, "confirm", lambda msg: False)
    review_preflight.check_pending_review("owner/repo", "1", force=False)
    assert api.call_count == 1
    assert api.call_args.kwargs.get("method") is None


def test_check_pending_review_confirm_deletes(monkeypatch):
    monkeypatch.setattr(review_preflight.gh_client, "login", lambda: "me")
    monkeypatch.setattr(
        review_preflight.gh_client, "api_json", lambda *a, **kw: {"id": 99},
    )
    api = MagicMock(return_value=SimpleNamespace(ok=True, stdout="3\n"))
    monkeypatch.setattr(review_preflight.gh_client, "api", api)
    monkeypatch.setattr(review_preflight.prompt, "confirm", lambda msg: True)
    review_preflight.check_pending_review("owner/repo", "1", force=False)
    assert api.call_count == 2
    assert api.call_args.kwargs.get("method") == "DELETE"


def test_check_pending_review_delete_failure_warns(monkeypatch, capsys):
    monkeypatch.setattr(review_preflight.gh_client, "login", lambda: "me")
    monkeypatch.setattr(
        review_preflight.gh_client, "api_json", lambda *a, **kw: {"id": 99},
    )
    api = MagicMock(return_value=SimpleNamespace(ok=False, stdout="3\n"))
    monkeypatch.setattr(review_preflight.gh_client, "api", api)
    monkeypatch.setattr(review_preflight.prompt, "confirm", lambda msg: True)
    review_preflight.check_pending_review("owner/repo", "1", force=False)
    assert "Could not delete the pending review" in capsys.readouterr().err


# ── check_stale_review ────────────────────────────────────────────────────────


def _write_review(tmp_path: Path, sha: str = "abc123") -> Path:
    review_file = tmp_path / "review.md"
    review_file.write_text(f"# Review\n<!-- head_sha: {sha} -->\n## Summary\n")
    return review_file


def test_check_stale_review_force_skips(tmp_path, monkeypatch):
    review_file = _write_review(tmp_path)
    get_head = MagicMock(side_effect=AssertionError("get_pr_head_sha called"))
    monkeypatch.setattr(review_preflight.review_recover, "get_pr_head_sha", get_head)
    review_preflight.check_stale_review("owner/repo", "1", review_file, force=True)
    assert not get_head.called


def test_check_stale_review_missing_file_is_silent(tmp_path, monkeypatch):
    get_head = MagicMock(side_effect=AssertionError("get_pr_head_sha called"))
    monkeypatch.setattr(review_preflight.review_recover, "get_pr_head_sha", get_head)
    review_preflight.check_stale_review(
        "owner/repo", "1", tmp_path / "nope.md", force=False,
    )
    assert not get_head.called


def test_check_stale_review_auto_recovers_on_failures(tmp_path, monkeypatch):
    """Same HEAD + pipeline failures → no prompt, returns silently (auto-recover)."""
    review_file = _write_review(tmp_path)
    (tmp_path / "pipeline.json").write_text(json.dumps({
        "head_sha": "abc123", "group_names": ["g1", "g2"],
        "done": ["synthesis"], "failed": {},
        "groups_done": [1], "groups_failed": {"2": "quota exhausted (429)"},
    }))
    monkeypatch.setattr(
        review_preflight.review_recover, "get_pr_head_sha",
        lambda *a, **kw: "abc123",
    )
    monkeypatch.setattr(
        review_preflight.prompt, "confirm",
        MagicMock(side_effect=AssertionError("confirm called unexpectedly")),
    )
    review_preflight.check_stale_review("owner/repo", "1", review_file, force=False)


def test_check_stale_review_prompts_on_clean_same_head(tmp_path, monkeypatch):
    """Same HEAD + no failures → still prompts 'Re-review anyway?'."""
    review_file = _write_review(tmp_path)
    (tmp_path / "pipeline.json").write_text(json.dumps({
        "head_sha": "abc123", "group_names": ["g1"],
        "done": ["synthesis", "disprove"], "failed": {},
        "groups_done": [1], "groups_failed": {},
    }))
    monkeypatch.setattr(
        review_preflight.review_recover, "get_pr_head_sha",
        lambda *a, **kw: "abc123",
    )
    monkeypatch.setattr(review_preflight.prompt, "confirm", lambda msg: False)
    with pytest.raises(SystemExit) as exc:
        review_preflight.check_stale_review(
            "owner/repo", "1", review_file, force=False,
        )
    assert exc.value.code == 0


def test_check_stale_review_incremental_does_not_prompt(tmp_path, monkeypatch, capsys):
    review_file = _write_review(tmp_path, sha="aaa111")
    monkeypatch.setattr(
        review_preflight.review_recover, "get_pr_head_sha",
        lambda *a, **kw: "bbb222",
    )
    monkeypatch.setattr(
        review_preflight.prompt, "confirm",
        MagicMock(side_effect=AssertionError("confirm called unexpectedly")),
    )
    review_preflight.check_stale_review("owner/repo", "1", review_file, force=False)
    assert "Incremental review" in capsys.readouterr().err


# ── supersession_override / refuse_if_superseded ──────────────────────────────


def _refuse(monkeypatch, verdict, *, override=False, trail=None):
    """Run the refusal against a canned verdict, detection already answered."""
    monkeypatch.setattr(
        review_preflight.supersession, "detect_cached",
        MagicMock(return_value=verdict),
    )
    review_preflight.refuse_if_superseded(
        "/wt", "acme/widget", Path("/target"), "feat/x",
        override=override, trail=trail or MagicMock(),
    )


def test_a_clean_branch_is_reviewed(monkeypatch, capsys):
    _refuse(monkeypatch, supersession_verdict())
    assert capsys.readouterr().err == ""


def test_context_alone_does_not_refuse(monkeypatch, capsys):
    """A rebase over a moved base is how the problem becomes visible, not the problem."""
    _refuse(monkeypatch, supersession_verdict(supersession_context()))
    assert "[rebase_skew] replayed onto a moved base" in capsys.readouterr().err


def test_evidence_refuses_before_the_first_agent_call(monkeypatch, capsys):
    """The whole point of refusing here: a review is the largest spend in the repo."""
    from pr import supersession

    with pytest.raises(SystemExit) as exc:
        _refuse(monkeypatch, supersession_verdict(supersession_evidence()))
    assert exc.value.code == supersession.EXIT_SUPERSEDED

    out = capsys.readouterr()
    assert "Refusing to review feat/x" in out.err
    assert supersession.OVERRIDE_FLAG in out.err
    payload = json.loads(out.out)
    assert payload["status"] == "superseded"
    assert payload["branch"] == "feat/x"
    assert payload["override"] == supersession.OVERRIDE_FLAG
    assert payload["signals"] == [{
        "kind": "readds_removed_symbol",
        "detail": "`foo` is gone from origin/main",
        "holds": True,
    }]


def test_the_refusal_reaches_the_trail(monkeypatch):
    trail = MagicMock()
    with pytest.raises(SystemExit):
        _refuse(monkeypatch, supersession_verdict(supersession_evidence()),
                trail=trail)
    data = trail.decision.call_args.kwargs["data"]
    assert data["signals"] == ["readds_removed_symbol"]


def test_the_override_skips_the_check_entirely(monkeypatch):
    """The override has to cost nothing, or it is not an override."""
    detect = MagicMock()
    monkeypatch.setattr(review_preflight.supersession, "detect_cached", detect)
    review_preflight.refuse_if_superseded(
        "/wt", "acme/widget", Path("/target"), "feat/x",
        override=True, trail=MagicMock(),
    )
    assert not detect.called


def test_only_the_users_own_force_overrides_the_refusal():
    """An unattended run is the one this refusal most has to survive.

    `--post` and `--no-post` set the `force` local that suppresses the
    confirmation prompts, because nobody is there to answer one. Letting that
    reach here would disarm the check on exactly the runs that post findings to
    a PR with no operator reading them first.
    """
    assert review_preflight.supersession_override(False, False) is False
    assert review_preflight.supersession_override(True, False) is True


def test_recover_overrides_the_refusal_on_both_paths():
    """Recovery finishes a run whose spend was already made."""
    assert review_preflight.supersession_override(False, True) is True
