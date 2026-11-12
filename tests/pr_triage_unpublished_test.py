"""Triage verdicts formed against unpublished code cannot be posted.

`already_addressed` and `invalid` are claims about code the reviewer can open.
A dirty, untracked, or unpushed tree is not that, even when the cited file
itself is clean — the strict rule is any unpublished path. These go through
`run_triage` so deleting the demotion call is what fails them.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from batch_git_support import remote_and_clone
from conftest import commit_all
from review_threads_support import _no_published_summary  # noqa: F401
import agent.backend
import pr.triage
from pr.thread_models import PRReport, ReportThread, Verification


def _report(path="feat.txt"):
    return PRReport(
        repo="owner/repo", pr_number=1,
        threads=[ReportThread(
            id="t1", file=path, line=1, reviewer="kgn",
            comments=[{"user": "kgn", "body": "do the thing", "databaseId": 1}],
        )],
    )


def _reply(verification="already_addressed", path="feat.txt"):
    return json.dumps({"threads": [{
        "id": "t1",
        "classification": "actionable_suggestion",
        "verification": verification,
        "complexity": "low",
        "summary": "s",
        "file": path,
        "line": 1,
        "evidence_file": path,
        "evidence_line": 1,
        "reasoning": "looks done",
    }]})


def _triage(work, *, verification="already_addressed", path="feat.txt"):
    with patch.object(agent.backend, "prompt",
                      return_value=(_reply(verification, path), 0)):
        result, rc = pr.triage.run_triage(_report(path), work, {})
    assert rc == 0
    assert result is not None
    return result.threads[0]


def _assert_demoted(entry):
    assert entry.verification is Verification.NEEDS_DISCUSSION
    assert pr.triage.UNPUBLISHED_DOWNGRADE_REASON in entry.reasoning


class TestUnpublishedTreeDemotesClaimVerdicts:
    def test_a_dirty_cited_file_demotes_already_addressed(self, tmp_path):
        pair = remote_and_clone(tmp_path)
        (pair.work / "feat.txt").write_text("feat\ndirty\n")
        _assert_demoted(_triage(pair.work))

    def test_a_dirty_cited_file_demotes_invalid(self, tmp_path):
        pair = remote_and_clone(tmp_path)
        (pair.work / "feat.txt").write_text("feat\ndirty\n")
        _assert_demoted(_triage(pair.work, verification="invalid"))

    def test_an_untracked_cited_file_is_demoted(self, tmp_path):
        pair = remote_and_clone(tmp_path)
        (pair.work / "extra.txt").write_text("extra\n")
        _assert_demoted(_triage(pair.work, path="extra.txt"))

    def test_an_unpushed_commit_touching_evidence_is_demoted(self, tmp_path):
        pair = remote_and_clone(tmp_path)
        (pair.work / "feat.txt").write_text("feat\nmore\n")
        commit_all(pair.work, "more")
        _assert_demoted(_triage(pair.work))

    def test_a_dirty_file_elsewhere_still_demotes(self, tmp_path):
        """Strict: unpublished anywhere, not only on the cited path."""
        pair = remote_and_clone(tmp_path)
        (pair.work / "leftover.py").write_text("x = 1\n")
        _assert_demoted(_triage(pair.work))

    # passes-at-base: a published tree never demoted these verdicts
    def test_a_clean_pushed_tree_keeps_already_addressed(self, tmp_path):
        pair = remote_and_clone(tmp_path)
        entry = _triage(pair.work)
        assert entry.verification is Verification.ALREADY_ADDRESSED

    # passes-at-base: valid verdicts were never in the demotion set
    def test_an_actionable_verdict_is_untouched(self, tmp_path):
        pair = remote_and_clone(tmp_path)
        (pair.work / "leftover.py").write_text("x = 1\n")
        entry = _triage(pair.work, verification="valid")
        assert entry.verification is Verification.VALID
