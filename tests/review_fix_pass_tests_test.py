"""Tests for the review fix pass owing a test beside a behavioural fix.

`fix_regression_test` holds what the check decides and `fix_engine_regression_test`
when the engine runs it. Here it is the review's half end to end: a real repo, a
real commit, and a body that does not call an untested change verified.
"""

import sys
from pathlib import Path
from unittest.mock import patch

from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import fix.gate  # noqa: E402
import fix.tracking  # noqa: E402
import review.fix  # noqa: E402
from review_record_support import finding, record  # noqa: E402

from review_fix_pass_support import (  # noqa: E402, F401
    _PUSHED, _committed_paths, _make_job, _run, git_wt,
)

REVIEW = record(finding("M1", "Missing helper", path="helper.py", checkbox=True))


def _edit_helper(wt, *, with_test):
    def work():
        (wt / "helper.py").write_text("def helper(): pass\n")
        if with_test:
            (wt / "helper_test.py").write_text("def test_helper(): pass\n")
    return work


class TestAFixOwesATest:
    @patch("git.push.push", return_value=_PUSHED)
    def test_an_untested_behavioural_fix_is_committed_but_hedged(
        self, mock_push, git_wt, tmp_path,
    ):
        job = _make_job(git_wt, tmp_path, rec=REVIEW)
        _run(job, {"M1": "fixed — Test: test_helper — asserts it"},
             work=_edit_helper(git_wt, with_test=False),
             verdicts={"M1": fix.gate.Verdict(ok=True, detail="looks right")})

        assert _committed_paths(git_wt) == {"helper.py"}
        msg = git_out(git_wt, "log", "-1", "--format=%B")
        assert "no test for helper.py in this commit" in msg
        assert "1 without a test" in msg
        assert "*(unverified — no test for helper.py" in Path(job.review_file).read_text()

    @patch("git.push.push", return_value=_PUSHED)
    def test_the_same_fix_with_its_test_is_not_hedged(
        self, mock_push, git_wt, tmp_path,
    ):
        job = _make_job(git_wt, tmp_path, rec=REVIEW)
        _run(job, {"M1": "fixed — Test: test_helper — asserts it"},
             work=_edit_helper(git_wt, with_test=True),
             verdicts={"M1": fix.gate.Verdict(ok=True, detail="looks right")})

        assert _committed_paths(git_wt) == {"helper.py", "helper_test.py"}
        msg = git_out(git_wt, "log", "-1", "--format=%B")
        assert "no test for" not in msg
        assert "- [x] **[M1]**" in Path(job.review_file).read_text()

    @patch("git.push.push", return_value=_PUSHED)
    def test_an_exemption_with_a_reason_is_not_hedged(
        self, mock_push, git_wt, tmp_path,
    ):
        job = _make_job(git_wt, tmp_path, rec=REVIEW)
        _run(job, {"M1": "fixed — No test: log wording"},
             work=_edit_helper(git_wt, with_test=False),
             verdicts={"M1": fix.gate.Verdict(ok=True, detail="looks right")})

        assert "no test for" not in git_out(git_wt, "log", "-1", "--format=%B")

    @patch("git.push.push", return_value=_PUSHED)
    def test_the_agent_is_told_the_exact_exemption_prefix(
        self, mock_push, git_wt, tmp_path,
    ):
        job = _make_job(git_wt, tmp_path, rec=REVIEW)
        inv = _run(job, {"M1": "fixed — No test: x"},
                   work=_edit_helper(git_wt, with_test=False))

        prompt = inv.call_args.args[1]
        assert f"`{fix.tracking.NO_TEST_PREFIX}`" in prompt
        assert "${no_test_prefix}" not in prompt

    def test_the_review_pass_asks_for_tests(self):
        assert review.fix.ReviewFixAdapter.requires_tests is True

    def test_static_violations_are_exempt_and_findings_are_not(
        self, git_wt, tmp_path,
    ):
        from review.static_analysis import StaticViolation
        job = _make_job(git_wt, tmp_path, rec=REVIEW)
        adapter = review.fix.ReviewFixAdapter(
            job, [], [StaticViolation(file="helper.py", line=1, message="deep", id="SA1")])
        assert adapter.test_exempt_ids() == frozenset({"SA1"})
