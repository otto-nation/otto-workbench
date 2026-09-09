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
    """The second rung runs only when there is something an agent could repair.

    It is injected rather than imported while the pre-push fix loop is still in
    the binary, so these pin that a caller passing nothing is not a crash.
    """

    @staticmethod
    def _owner_reports(result):
        return mock.patch.object(land, "land_head", return_value=result)

    def test_a_refusal_hands_the_output_to_the_fix(self):
        repaired = _pushed(sha="9f8e7d6")
        fix = mock.Mock(return_value=repaired)

        with self._owner_reports(_refused()):
            got = rebase_land.land_rebased(
                "/fake", resolved_files=["server.go"], on_check_failure=fix,
            )

        assert got is repaired
        fix.assert_called_once_with("/fake", "gofmt: server.go", ["server.go"])

    def test_a_fix_that_produced_nothing_leaves_the_refusal_standing(self):
        refusal = _refused()
        with self._owner_reports(refusal):
            got = rebase_land.land_rebased(
                "/fake", resolved_files=["server.go"],
                on_check_failure=mock.Mock(return_value=None),
            )
        assert got is refusal

    def test_no_fix_injected_leaves_the_refusal_standing(self):
        """A caller with no repair to offer gets the push's own verdict back."""
        refusal = _refused()
        with self._owner_reports(refusal):
            assert rebase_land.land_rebased(
                "/fake", resolved_files=["server.go"],
            ) is refusal

    def test_a_landed_push_never_reaches_the_fix(self):
        fix = mock.Mock()
        with self._owner_reports(_pushed()):
            assert rebase_land.land_rebased(
                "/fake", resolved_files=["server.go"], on_check_failure=fix,
            ).ok
        fix.assert_not_called()

    def test_no_resolved_files_skips_the_fix(self):
        """Nothing the AI resolved means nothing it has standing to repair."""
        fix = mock.Mock()
        with self._owner_reports(_refused()):
            rebase_land.land_rebased("/fake", on_check_failure=fix)
        fix.assert_not_called()

    def test_an_empty_complaint_skips_the_fix(self):
        """An empty complaint is not a prompt — the agent would be guessing."""
        fix = mock.Mock()
        with self._owner_reports(_refused(error="")):
            rebase_land.land_rebased(
                "/fake", resolved_files=["server.go"], on_check_failure=fix,
            )
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
