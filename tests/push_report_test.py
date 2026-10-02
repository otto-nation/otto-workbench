"""git.push reporting: the resume command, the report text and the bash bridge."""

import json
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
import core.workbench_paths  # noqa: E402

from conftest import _last_event  # noqa: E402

# `pushable` is a fixture: imported so pytest finds it here, never called by name.
from push_support import _commit, pushable, _lose_pushes, _RESET_DUMP_FULL


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


# ── the bash bridge ─────────────────────────────────────────────────────────


def test_cli_exit_codes_cover_every_status():
    """A status with no exit code would raise KeyError at the worst moment."""
    assert set(git.push._EXIT_CODES) == set(git.push.PushStatus)


def test_cli_exits_zero_on_a_verified_push(pushable):
    wt, _ = pushable
    _commit(wt, "work")
    assert git.push.main(["--cwd", str(wt), "--branch", "main"]) == 0


def test_cli_reports_a_lost_push(pushable, capsys):
    wt, remote = pushable
    _commit(wt, "work")
    _lose_pushes(remote)
    assert git.push.main(["--cwd", str(wt), "--branch", "main"]) == 2
    assert "the remote did not move" in capsys.readouterr().err


def test_cli_exits_one_when_git_refuses(pushable, monkeypatch):
    """A refusal and a lost push get different codes so bash can tell them apart."""
    wt, _ = pushable
    _commit(wt, "work")
    monkeypatch.setattr(
        git.client, "run",
        lambda *cmd, **kw: core.proc.CmdResult(1, "", "error: failed to push some refs"),
    )
    assert git.push.main(["--cwd", str(wt), "--branch", "main"]) == 1


def test_cli_exits_three_when_the_remote_cannot_be_asked(pushable, monkeypatch):
    """Unverified is its own code — the shell warns rather than aborting."""
    wt, _ = pushable
    _commit(wt, "work")
    monkeypatch.setattr(git.push, "remote_head", lambda *a, **k: None)
    assert git.push.main(["--cwd", str(wt), "--branch", "main"]) == 3


def _trail_events() -> list[dict]:
    """Every record in the sandboxed trail root, not only the last.

    `conftest._last_event` answers for a run whose final event is the subject;
    these assert that a particular event is *somewhere* in the run, which the
    last one alone cannot show — `finish` always follows it.
    """
    root = core.workbench_paths.trail_dir()
    return [json.loads(line)
            for p in sorted(root.glob("*.jsonl"))
            for line in p.read_text().splitlines() if line.strip()]


_FAILING_GATE = """#!/usr/bin/env bash
echo "→ Running pytest (203/203 files)..."
echo "FAILED tests/tree_lock_test.py::test_a_signal_racing_the_spawn"
echo "✗ Pytest failed"
exit 1
"""


def _refusing_gate(wt: Path) -> None:
    """Install a pre-push hook that fails the way the workbench gate does."""
    hook = wt / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(_FAILING_GATE)
    hook.chmod(0o755)


def test_cli_keeps_the_whole_gate_output_when_the_hook_refuses(pushable):
    """The bash bridge is an entry point, so it opens the trail that keeps it.

    Without one, a gate that ran for half an hour and printed the only copy of
    a rare test failure leaves a 20-line excerpt on a terminal and nothing on
    disk — which is how one such failure was lost. Driven through a real
    refusing hook rather than a stubbed `git.client.run`, because what is under
    test is that the hook's own words reach the artifact.
    """
    wt, _ = pushable
    _commit(wt, "work")
    _refusing_gate(wt)

    assert git.push.main(["--cwd", str(wt), "--branch", "main"]) == 1

    artifacts = sorted(core.workbench_paths.trail_dir().glob("artifacts/*/*-push.log"))
    assert len(artifacts) == 1, "the refusal left no artifact to diagnose from"
    kept = artifacts[0].read_text()
    assert "test_a_signal_racing_the_spawn" in kept, (
        "the artifact holds the gate's banner but not the failure under it"
    )
    assert "✗ Pytest failed" in kept


def test_cli_names_the_artifact_it_wrote(pushable, capsys):
    """An artifact nobody is told about is one nobody reads."""
    wt, _ = pushable
    _commit(wt, "work")
    _refusing_gate(wt)

    git.push.main(["--cwd", str(wt), "--branch", "main"])

    assert "full output: " in capsys.readouterr().err


def test_cli_records_the_branch_it_pushed(pushable):
    """`otto-log --repo` filters on context, so an unlabelled trail is unfindable."""
    wt, _ = pushable
    _commit(wt, "work")
    _refusing_gate(wt)

    git.push.main(["--cwd", str(wt), "--branch", "main"])

    assert _last_event()["context"]["branch"] == "main"


def test_cli_records_an_unexpected_exception(pushable, monkeypatch):
    """`finish` writes one summary with no verdict, so a crash needs its own event.

    Without it the trail of a run that died mid-push reads exactly like the
    trail of a clean one, which is the wrong-reporting failure a trail exists
    to prevent.
    """
    wt, _ = pushable
    _commit(wt, "work")

    def boom(*_args, **_kwargs):
        raise RuntimeError("the remote fell over")

    monkeypatch.setattr(git.push, "push", boom)

    with pytest.raises(RuntimeError):
        git.push.main(["--cwd", str(wt), "--branch", "main"])

    crashes = [e for e in _trail_events() if e["action"] == "unexpected_error"]
    assert crashes, "the crash left a trail indistinguishable from a clean run"
    assert "the remote fell over" in crashes[0]["detail"]
    assert "RuntimeError" in crashes[0]["data"]["traceback"]


def test_script_imports_with_pythonpath_overwritten(tmp_path):
    """push.py runs as a script under an interpreter whose PYTHONPATH points elsewhere.

    A mise shim assigns PYTHONPATH from the workspace's own [env] before exec'ing
    python, replacing anything the caller exported. Passing ai/lib in from the
    shell therefore cannot be relied on, so the script puts it on sys.path itself.
    The hostile value here stands in for that overwrite.
    """
    result = core.proc.run(
        [sys.executable, str(LIB_DIR / "git" / "push.py"), "--help"],
        timeout=30,
        env={"PYTHONPATH": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert "usage: push.py" in result.stdout
