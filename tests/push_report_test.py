"""git.push reporting: the resume command and the report text."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.client  # noqa: E402
import core.proc  # noqa: E402
import git.push  # noqa: E402

from push_support import _RESET_DUMP_FULL  # noqa: E402


# ── the resume command ──────────────────────────────────────────────────────


def _result(status, refusal=None, sha="1a2b3c4d", branch="feat/x", args=()):
    return git.push.PushResult(status, sha=sha, branch=branch, refusal=refusal, args=args)


def test_a_landed_push_needs_no_resume():
    assert git.push.resume_command(_result(git.push.PushStatus.PUSHED), "/tmp/wt") == ""


@pytest.mark.parametrize("status", [s for s in git.push.PushStatus if s is not
                                    git.push.PushStatus.PUSHED])
def test_every_unfinished_status_names_a_command(status):
    """Rule 2 of the land owner: nothing falls short without saying what finishes it."""
    resume = git.push.resume_command(_result(status), "/tmp/wt")
    assert resume.startswith("git -C '/tmp/wt' ")


@pytest.mark.parametrize("refusal", list(git.push.Refusal))
def test_every_refusal_names_a_command(refusal):
    """A refusal kind with no answer would render an empty "Resume:" line."""
    assert git.push.resume_command(
        _result(git.push.PushStatus.REFUSED, refusal), "/tmp/wt",
    ).startswith("git -C '/tmp/wt' push")


def test_a_divergence_answers_force_with_lease():
    assert git.push.resume_command(
        _result(git.push.PushStatus.REFUSED, git.push.Refusal.DIVERGED), "/tmp/wt",
    ) == "git -C '/tmp/wt' push --force-with-lease"


@pytest.mark.parametrize("refusal", [git.push.Refusal.HOOK, git.push.Refusal.TRANSPORT,
                                     git.push.Refusal.AUTH, git.push.Refusal.UNREACHABLE,
                                     git.push.Refusal.DROPPED, git.push.Refusal.OTHER])
def test_no_other_refusal_answers_a_force_push(refusal):
    """A pre-push hook rejection is not divergence — force-pushing is wrong advice."""
    assert "--force" not in git.push.resume_command(
        _result(git.push.PushStatus.REFUSED, refusal), "/tmp/wt",
    )


def test_an_unverified_push_is_checked_rather_than_repushed():
    """It has very likely landed; `ls-remote` answers in one round trip."""
    result = git.push.PushResult(
        git.push.PushStatus.UNVERIFIED, sha="1a2b3c4d", branch="feat/x", remote="upstream",
    )
    assert git.push.resume_command(result, "/tmp/wt") == (
        "git -C '/tmp/wt' ls-remote upstream feat/x"
    )


def test_the_resume_replays_what_the_push_was_given():
    """`pr rebase` pushes with a lease; a plain resume is refused a second time."""
    result = _result(git.push.PushStatus.REFUSED, git.push.Refusal.HOOK,
                     args=("--force-with-lease",))
    assert git.push.resume_command(result, "/tmp/wt") == (
        "git -C '/tmp/wt' push --force-with-lease"
    )


def test_a_divergence_does_not_repeat_a_lease_the_push_already_carried():
    result = _result(git.push.PushStatus.REFUSED, git.push.Refusal.DIVERGED,
                     args=("--force-with-lease",))
    assert git.push.resume_command(result, "/tmp/wt").count("--force-with-lease") == 1


def test_a_push_records_the_arguments_it_was_given(monkeypatch):
    """Nothing can replay them later unless the result carried them out."""
    monkeypatch.setattr(git.client, "run",
                        lambda *a, **k: core.proc.CmdResult(1, "", "failed to push some refs"))
    result = git.push.push("/tmp/wt", gated=False, sha="1a2b3c4d", branch="feat/x",
                       args=["--force-with-lease"])
    assert result.args == ("--force-with-lease",)


def test_the_resume_command_quotes_a_worktree_with_a_space():
    """Unquoted, the path splits and the command runs somewhere else or nowhere."""
    resume = git.push.resume_command(_result(git.push.PushStatus.LOST), "/tmp/my wt")
    assert "'/tmp/my wt'" in resume


# ── the report ──────────────────────────────────────────────────────────────


def test_an_unverified_push_git_reported_as_clean_says_it_was_pushed(capsys):
    """git did report success here, so the only open question is confirmation."""
    result = git.push.PushResult(
        git.push.PushStatus.UNVERIFIED, sha="1a2b3c4d", branch="feat/x",
    )
    git.push.report(result, "/tmp/wt")

    printed = capsys.readouterr().err
    assert "pushed 1a2b3c4" in printed
    assert "ls-remote" in printed


def test_an_unverified_dropped_push_does_not_claim_it_was_pushed(capsys):
    """`git push` itself failed here; nothing has established the commit was sent.

    The drop is why the remote was asked rather than the operator being told
    their checks failed — and the remote then could not answer either. Reusing
    the ordinary wording tells the reader git pushed it, which is the misreport
    this classification exists to remove.
    """
    result = git.push.PushResult(
        git.push.PushStatus.UNVERIFIED, sha="1a2b3c4d", branch="feat/x",
        refusal=git.push.Refusal.DROPPED,
    )
    git.push.report(result, "/tmp/wt")

    printed = capsys.readouterr().err
    assert "pushed 1a2b3c4" not in printed
    assert "connection dropped" in printed
    assert "ls-remote" in printed


def test_an_unverified_push_reports_the_retry_that_ran(capsys):
    """A retry whose verification failed too is still a retry that ran.

    Left unsaid, the reader counts one transfer where two were made and reads a
    `--no-verify` push as one that never happened.
    """
    result = git.push.PushResult(
        git.push.PushStatus.UNVERIFIED, sha="1a2b3c4d", branch="feat/x",
        refusal=git.push.Refusal.DROPPED, retry=git.push.Retry.ATTEMPTED,
    )
    git.push.report(result, "/tmp/wt")

    assert "Retried once" in capsys.readouterr().err


def test_an_unverified_push_that_never_retried_says_nothing_about_retries(capsys):
    """The common warning stays one line; there is no retry to account for."""
    result = git.push.PushResult(
        git.push.PushStatus.UNVERIFIED, sha="1a2b3c4d", branch="feat/x",
    )
    git.push.report(result, "/tmp/wt")

    assert "etried" not in capsys.readouterr().err


def test_refused_report_trims_a_whole_test_suite_to_its_tail(capsys):
    """A failing pre-push prints its entire suite; the tail is what named it."""
    output = "\n".join(f"line {n}" for n in range(200))
    result = git.push.PushResult(
        git.push.PushStatus.REFUSED, sha="1a2b3c4d", branch="feat/x",
        refusal=git.push.Refusal.HOOK, output=output,
    )
    git.push.report(result, "/tmp/wt")

    printed = capsys.readouterr().err
    assert "line 199" in printed
    assert "line 0" not in printed
    assert printed.count("line ") == core.proc.TAIL_LINES


def test_lost_report_names_the_branch_the_commit_and_the_remote(capsys):
    result = git.push.PushResult(
        git.push.PushStatus.LOST, sha="1a2b3c4d5e", branch="feat/x",
        remote_sha="9f8e7d6c5b", retry=git.push.Retry.ATTEMPTED,
    )
    git.push.report(result, "/tmp/wt")

    printed = capsys.readouterr().err
    assert "the remote did not move" in printed
    assert "feat/x" in printed
    assert "1a2b3c4" in printed
    assert "9f8e7d6" in printed
    assert "Retried once" in printed


def test_lost_report_says_when_the_remote_holds_no_such_ref(capsys):
    result = git.push.PushResult(
        git.push.PushStatus.LOST, sha="1a2b3c4d", branch="feat/x", remote_sha="",
    )
    git.push.report(result, "/tmp/wt")

    printed = capsys.readouterr().err
    assert "no such ref" in printed
    assert "Not retried." in printed


def test_a_dropped_push_that_landed_says_the_connection_dropped(capsys):
    """The terminal is full of red ssh diagnostics; the commit arrived anyway."""
    result = git.push.PushResult(
        git.push.PushStatus.PUSHED, sha="1a2b3c4d", branch="feat/x",
        remote_sha="1a2b3c4d", refusal=git.push.Refusal.DROPPED,
    )
    git.push.report(result, "/tmp/wt")

    printed = capsys.readouterr().err
    assert "connection dropped" in printed
    assert "1a2b3c4" in printed


def test_a_dropped_push_does_not_claim_git_reported_success(capsys):
    """git reporting success is the one thing that did not happen here."""
    result = git.push.PushResult(
        git.push.PushStatus.LOST, sha="1a2b3c4d", branch="feat/x",
        refusal=git.push.Refusal.DROPPED, retry=git.push.Retry.ATTEMPTED,
    )
    git.push.report(result, "/tmp/wt")

    printed = capsys.readouterr().err
    assert "reported success" not in printed
    assert "connection dropped" in printed


def test_a_lost_push_git_reported_as_clean_still_says_so(capsys):
    """The classic lost push is the one git *did* report as a success."""
    result = git.push.PushResult(
        git.push.PushStatus.LOST, sha="1a2b3c4d", branch="feat/x",
    )
    git.push.report(result, "/tmp/wt")

    assert "reported success" in capsys.readouterr().err


def test_a_dropped_refusal_does_not_claim_nothing_reached_the_remote(capsys):
    """Whether anything reached it is exactly what a drop leaves unestablished."""
    result = git.push.PushResult(
        git.push.PushStatus.REFUSED, sha="1a2b3c4d", branch="feat/x",
        refusal=git.push.Refusal.DROPPED, output=_RESET_DUMP_FULL,
    )
    git.push.report(result, "/tmp/wt")

    assert "nothing reached the remote" not in capsys.readouterr().err


def test_the_dropped_report_quotes_what_killed_the_push(capsys):
    """A LOST report prints no output normally; here the signal is the account."""
    result = git.push.PushResult(
        git.push.PushStatus.LOST, sha="1a2b3c4d", branch="feat/x",
        refusal=git.push.Refusal.DROPPED,
        output="git was killed by SIGPIPE (signal 13)",
    )
    git.push.report(result, "/tmp/wt")

    assert "SIGPIPE" in capsys.readouterr().err


def test_every_retry_state_has_a_report_line():
    """A LOST report claiming a retry that never ran is the wrong-reporting
    failure this module exists to remove."""
    assert set(git.push._RETRY_NOTE) == set(git.push.Retry)
