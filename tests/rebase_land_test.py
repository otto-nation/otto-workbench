"""Tests for rebase.land — the force-push and its two recovery rungs."""

import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from git import land
from git import push
from git.land import CommitStatus
from rebase import land as rebase_land
from rebase import prepush


def _pushed(sha="1a2b3c4"):
    return land.LandResult(CommitStatus.PUSHED, sha=sha)


def _refused(error="gofmt: server.go"):
    return land.LandResult(
        CommitStatus.PUSH_FAILED, sha="1a2b3c4", error=error,
        push=push.PushResult(
            push.PushStatus.REFUSED, sha="1a2b3c4", branch="isaac/feat/x",
            refusal=push.Refusal.HOOK, output=error,
        ),
    )


class TestCheckFailureSeam:
    """The second rung runs only when there is something an agent could repair."""

    @staticmethod
    def _owner_reports(result):
        return mock.patch.object(land, "land_head", return_value=result)

    def test_a_refusal_hands_the_output_to_the_fix(self):
        repaired = _pushed(sha="9f8e7d6")

        with self._owner_reports(_refused()), \
             mock.patch.object(prepush, "fix_push_failures",
                               return_value=repaired) as fix:
            got = rebase_land.land_rebased("/fake", resolved_files=["server.go"])

        assert got is repaired
        fix.assert_called_once_with(
            "/fake", "gofmt: server.go", ["server.go"], trail=None)

    def test_a_fix_that_produced_nothing_leaves_the_refusal_standing(self):
        refusal = _refused()
        with self._owner_reports(refusal), \
             mock.patch.object(prepush, "fix_push_failures", return_value=None):
            got = rebase_land.land_rebased("/fake", resolved_files=["server.go"])
        assert got is refusal

    def test_a_landed_push_never_reaches_the_fix(self):
        with self._owner_reports(_pushed()), \
             mock.patch.object(prepush, "fix_push_failures") as fix:
            assert rebase_land.land_rebased(
                "/fake", resolved_files=["server.go"]).ok
        fix.assert_not_called()

    def test_no_resolved_files_skips_the_fix(self):
        """Nothing the AI resolved means nothing it has standing to repair."""
        with self._owner_reports(_refused()), \
             mock.patch.object(prepush, "fix_push_failures") as fix:
            rebase_land.land_rebased("/fake")
        fix.assert_not_called()

    def test_an_empty_complaint_skips_the_fix(self):
        """An empty complaint is not a prompt — the agent would be guessing."""
        with self._owner_reports(_refused(error="")), \
             mock.patch.object(prepush, "fix_push_failures") as fix:
            rebase_land.land_rebased("/fake", resolved_files=["server.go"])
        fix.assert_not_called()


class TestGatedPush:
    def test_it_asks_the_owner_for_a_gated_force_push(self):
        """The publishing gate, not an argument here, decides what reaches origin."""
        with mock.patch.object(
            land, "land_head", return_value=_pushed(),
        ) as owner:
            rebase_land.land_rebased("/fake")

        kwargs = owner.call_args.kwargs
        assert owner.call_args[0][0] == "/fake"
        assert kwargs["gated"] is True
        assert kwargs["args"] == rebase_land.FORCE_PUSH_ARGS
        assert kwargs["regen"] == rebase_land.REGEN_MESSAGE
