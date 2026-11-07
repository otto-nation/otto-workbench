"""File-only `--track` against an existing self-review record. No agent."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import make_ctx
from review.issue import CreatedIssue, IssueDelivery, IssueResult
from review.record import Disposition, load_record
from review_entry_support import reviews_dir
from review_record_support import finding, record, seed_record

import core.publishing
import review.file_tracked
import review.finding_issue
import review.issue

HEAD = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
REPO = "owner/repo"
BRANCH = "feat/test"


def _seed(reviews_dir, *ids, head_sha=HEAD, covered_sha="", **kw):
    findings = [
        finding(fid, f"finding {fid}", disposition=Disposition.UNREACHED)
        for fid in ids
    ]
    meta = dict(head_sha=head_sha, **kw)
    if covered_sha:
        meta["covered_sha"] = covered_sha
    return seed_record(
        reviews_dir, name="repo-self-feat-test",
        rec=record(*findings), **meta,
    )


def _ctx(head_sha=HEAD):
    return make_ctx(repo=REPO, branch=BRANCH, pr_number=None, head_sha=head_sha)


def _filed(review_dir, create):
    create.return_value = IssueResult(
        IssueDelivery.FILED, CreatedIssue("55", "https://example/55"),
    )
    with core.publishing.run(post=True):
        return review.file_tracked.run(_ctx(), frozenset({"M1"}))


def test_stale_record_refuses_and_files_nothing(reviews_dir, monkeypatch, capsys):
    """Revert: drop the SHA check — ids from another commit would be filed."""
    review_dir = _seed(reviews_dir, "M1", covered_sha=HEAD)
    create = MagicMock(side_effect=AssertionError("create_issue"))
    monkeypatch.setattr(review.issue, "create_issue", create)
    with core.publishing.run(post=True):
        rc = review.file_tracked.run(_ctx(OTHER), frozenset({"M1"}))
    assert rc == 1
    err = capsys.readouterr().err
    assert HEAD in err and OTHER in err
    assert [f.id for f in review.finding_issue.filable(review_dir)] == ["M1"]
    create.assert_not_called()


def test_matching_record_files_tracked_ids_once(reviews_dir, monkeypatch):
    """Revert: skip the `filed=` stamp / pass all ids — a second run would file again."""
    review_dir = _seed(reviews_dir, "M1", "S2", covered_sha=HEAD)
    create = MagicMock()
    monkeypatch.setattr(review.issue, "create_issue", create)
    provider = SimpleNamespace(name="github", options={}, resolved=True)
    monkeypatch.setattr(review.issue, "load_issue_provider", lambda *a, **k: provider)
    monkeypatch.setattr(review.issue, "ensure_issue_provider", lambda *a, **k: provider)
    monkeypatch.setattr(review.finding_issue, "note_on_parent", lambda *a, **k: None)

    assert _filed(review_dir, create) == 0
    assert create.call_count == 1
    body = create.call_args.args[3]
    assert "finding M1" in body and "finding S2" not in body
    assert [f.id for f in review.finding_issue.filable(review_dir)] == ["S2"]
    rec = load_record(review_dir)
    assert rec.finding("M1").filed == "55"

    assert _filed(review_dir, create) == 0
    assert create.call_count == 1


def test_unknown_id_is_refused(reviews_dir, monkeypatch, capsys):
    """Revert: skip `validate_track` — a typo would file nothing silently or file the rest."""
    review_dir = _seed(reviews_dir, "M1")
    create = MagicMock(side_effect=AssertionError("create_issue"))
    monkeypatch.setattr(review.issue, "create_issue", create)
    with core.publishing.run(post=True):
        rc = review.file_tracked.run(_ctx(), frozenset({"S9"}))
    assert rc == 1
    assert "S9" in capsys.readouterr().err
    create.assert_not_called()
    assert [f.id for f in review.finding_issue.filable(review_dir)] == ["M1"]


def test_without_post_does_not_call_create_issue(reviews_dir, monkeypatch):
    """Revert: drop the `publishing.enabled()` guard and always call `file_and_link`."""
    _seed(reviews_dir, "M1")
    create = MagicMock(side_effect=AssertionError("create_issue"))
    monkeypatch.setattr(review.issue, "create_issue", create)
    with core.publishing.run(post=False):
        assert review.file_tracked.run(_ctx(), frozenset({"M1"})) == 0
    create.assert_not_called()


def test_head_sha_matches_when_covered_sha_is_absent(reviews_dir, monkeypatch):
    review_dir = _seed(reviews_dir, "M1", head_sha=HEAD)
    create = MagicMock()
    monkeypatch.setattr(review.issue, "create_issue", create)
    provider = SimpleNamespace(name="github", options={}, resolved=True)
    monkeypatch.setattr(review.issue, "load_issue_provider", lambda *a, **k: provider)
    monkeypatch.setattr(review.issue, "ensure_issue_provider", lambda *a, **k: provider)
    monkeypatch.setattr(review.finding_issue, "note_on_parent", lambda *a, **k: None)
    assert _filed(review_dir, create) == 0
    assert create.call_count == 1
