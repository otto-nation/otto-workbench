"""Tests for the record every push leaves behind, and its reconciliation.

Against real repositories rather than a mocked `push.remote_head`, for the
reason `push_test` gives: the questions here are about what git answers — an
`ls-remote` for a ref that is gone, a `merge-base` between a recorded commit and
what the remote moved on to — and a mock would answer them the way this module
already assumes.

The two cases a real remote cannot be made to produce on demand are stubbed and
only those: an unreachable remote (pointed at a path that does not exist, which
is real enough) and the assertion that reconciliation touches nothing when the
file is absent, which is a claim about a call that must not happen.
"""

import io
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git_client  # noqa: E402
import push  # noqa: E402
import push_intent  # noqa: E402
import serde  # noqa: E402

from conftest import GIT_TIMEOUT, git_in, git_out, seed_repo  # noqa: E402

_ZERO = "0" * 40


def _ref(local_ref="refs/heads/main", local_sha="a" * 40,
         remote_ref="refs/heads/main", remote_sha=_ZERO) -> push_intent.PushedRef:
    """One line of pre-push stdin, with everything but the point defaulted."""
    return push_intent.PushedRef(local_ref, local_sha, remote_ref, remote_sha)


def _records() -> list[push_intent.PushIntent]:
    """What the state file holds right now."""
    return push_intent._load()


def _commit(wt: Path, message: str) -> str:
    """An empty commit in *wt*, returning its SHA."""
    git_in(wt, "-c", "user.name=t", "-c", "user.email=t@t",
           "commit", "-q", "--allow-empty", "--no-verify", "-m", message)
    return git_client.head_sha(cwd=wt)


@pytest.fixture
def pushable(tmp_path) -> tuple[Path, Path]:
    """A one-commit repo on `main`, with `origin` a bare repo alongside it."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True, timeout=GIT_TIMEOUT)
    wt = seed_repo(tmp_path / "wt")
    git_in(wt, "remote", "add", "origin", str(remote))
    git_in(wt, "push", "-q", "-u", "origin", "main")
    return wt, remote


# ── reading the hook's stdin ────────────────────────────────────────────────


def test_parse_refs_reads_the_lines_git_writes():
    refs = push_intent.parse_refs(
        "refs/heads/foo aaa refs/heads/bar bbb\n"
        "refs/heads/baz ccc refs/heads/baz ddd\n"
    )
    assert [(r.local_ref, r.local_sha, r.remote_ref, r.remote_sha) for r in refs] == [
        ("refs/heads/foo", "aaa", "refs/heads/bar", "bbb"),
        ("refs/heads/baz", "ccc", "refs/heads/baz", "ddd"),
    ]


@pytest.mark.parametrize("text", ["", "\n", "   \n", "one two three\n", "a b c d e\n"])
def test_parse_refs_drops_anything_that_is_not_four_fields(text):
    """Guessing at a line git did not write would record a push nobody made."""
    assert push_intent.parse_refs(text) == []


def test_deleted_reads_an_all_zero_sha_of_any_length():
    """A SHA-256 repository spells a delete with sixty-four zeros, not forty."""
    assert _ref(local_sha=_ZERO).deleted
    assert _ref(local_sha="0" * 64).deleted
    assert not _ref(local_sha="a" * 40).deleted
    assert not _ref(local_sha="").deleted


def test_branch_is_the_remote_side_of_the_refspec():
    """`git push origin foo:bar` moves `bar`, and nothing named `foo`."""
    assert _ref(local_ref="refs/heads/foo", remote_ref="refs/heads/bar").branch == "bar"


@pytest.mark.parametrize("remote_ref", ["refs/tags/v1", "refs/notes/commits", "HEAD", ""])
def test_branch_is_empty_for_anything_ls_remote_heads_cannot_answer(remote_ref):
    assert _ref(remote_ref=remote_ref).branch == ""


# ── recording ───────────────────────────────────────────────────────────────


def test_record_writes_the_remote_branch_and_the_full_refspec():
    push_intent.record([_ref(local_ref="refs/heads/foo", local_sha="c" * 40,
                             remote_ref="refs/heads/bar")],
                       repo="/w", remote="upstream")
    (intent,) = _records()
    assert (intent.repo, intent.remote, intent.branch, intent.sha) == (
        "/w", "upstream", "bar", "c" * 40)
    assert intent.refspec == "refs/heads/foo:refs/heads/bar"
    assert intent.attempts == 0
    assert intent.recorded


def test_record_writes_nothing_when_no_ref_is_verifiable():
    """A tag-only push leaves no file at all, so `reconcile` stays one stat."""
    push_intent.record([_ref(remote_ref="refs/tags/v1")], repo="/w", remote="origin")
    assert not push_intent.intents_path().exists()


def test_record_keeps_one_line_per_ref_of_a_multi_ref_push():
    push_intent.record(
        [_ref(local_ref="refs/heads/a", remote_ref="refs/heads/a"),
         _ref(local_ref="refs/heads/b", remote_ref="refs/heads/b")],
        repo="/w", remote="origin",
    )
    assert [i.branch for i in _records()] == ["a", "b"]


def test_a_second_push_to_the_same_branch_replaces_the_first():
    """`push A; push B` must never report A as the push that vanished."""
    push_intent.record([_ref(local_sha="a" * 40)], repo="/w", remote="origin")
    push_intent.record([_ref(local_sha="b" * 40)], repo="/w", remote="origin")
    assert [i.sha for i in _records()] == ["b" * 40]


def test_the_same_branch_in_another_repo_or_remote_is_a_different_record():
    push_intent.record([_ref()], repo="/w", remote="origin")
    push_intent.record([_ref()], repo="/other", remote="origin")
    push_intent.record([_ref()], repo="/w", remote="upstream")
    assert {(i.repo, i.remote) for i in _records()} == {
        ("/w", "origin"), ("/other", "origin"), ("/w", "upstream")}


def test_a_delete_drops_the_record_and_adds_none():
    """A branch removed on purpose has no commit left to verify."""
    push_intent.record([_ref(remote_ref="refs/heads/gone")], repo="/w", remote="origin")
    push_intent.record([_ref(local_ref="(delete)", local_sha=_ZERO,
                             remote_ref="refs/heads/gone")],
                       repo="/w", remote="origin")
    assert not push_intent.intents_path().exists()


def test_a_delete_leaves_other_branches_alone():
    push_intent.record([_ref(remote_ref="refs/heads/keep")], repo="/w", remote="origin")
    push_intent.record([_ref(local_sha=_ZERO, remote_ref="refs/heads/gone")],
                       repo="/w", remote="origin")
    assert [i.branch for i in _records()] == ["keep"]


def test_the_file_is_bounded_and_drops_the_oldest_first(monkeypatch):
    monkeypatch.setattr(push_intent, "_MAX_RECORDS", 3)
    for n in range(6):
        push_intent.record([_ref(remote_ref=f"refs/heads/b{n}")], repo="/w", remote="origin")
    assert [i.branch for i in _records()] == ["b3", "b4", "b5"]


def test_record_survives_a_state_root_it_cannot_write(monkeypatch, capsys):
    """Bookkeeping must warn and give up, never refuse somebody's push."""
    monkeypatch.setattr(push_intent, "intents_path",
                        lambda: Path("/proc/nonexistent/push-intents.json"))
    push_intent.record([_ref()], repo="/w", remote="origin")
    assert "could not update" in capsys.readouterr().err


# ── reconciling ─────────────────────────────────────────────────────────────


def test_reconcile_asks_nothing_when_no_push_is_pending(monkeypatch):
    """The ordinary case is one failed stat and no network."""
    monkeypatch.setattr(push, "remote_head", _never_asked)
    push_intent.reconcile()


def _never_asked(*args, **kwargs):
    raise AssertionError("the remote was asked about nothing")


def test_a_landed_push_drains_in_silence(pushable, capsys):
    wt, _ = pushable
    push_intent.record(
        [_ref(local_sha=git_client.head_sha(cwd=wt))], repo=str(wt), remote="origin")
    push_intent.reconcile()
    assert not push_intent.intents_path().exists()
    assert capsys.readouterr().err == ""


def test_a_remote_built_on_top_of_the_push_is_landed(pushable, capsys):
    """Somebody else's commit on the branch is not this push having vanished."""
    wt, _ = pushable
    landed = git_client.head_sha(cwd=wt)
    push_intent.record([_ref(local_sha=landed)], repo=str(wt), remote="origin")
    _commit(wt, "somebody elses commit")
    git_in(wt, "push", "-q", "origin", "main")
    push_intent.reconcile()
    assert not push_intent.intents_path().exists()
    assert capsys.readouterr().err == ""


def test_a_ref_the_remote_no_longer_has_is_reported_once(pushable, capsys):
    wt, remote = pushable
    sha = git_client.head_sha(cwd=wt)
    push_intent.record([_ref(local_sha=sha)], repo=str(wt), remote="origin")
    git_in(remote, "update-ref", "-d", "refs/heads/main")

    push_intent.reconcile()
    err = capsys.readouterr().err
    assert "nothing has confirmed" in err
    assert str(wt) in err
    assert "main" in err
    assert sha[:7] in err
    assert not push_intent.intents_path().exists()

    push_intent.reconcile()
    assert capsys.readouterr().err == ""


def test_a_remote_holding_a_different_commit_is_reported(pushable, capsys):
    """Not an ancestor and not equal: the push is gone and something else is not."""
    wt, remote = pushable
    first = git_client.head_sha(cwd=wt)
    second = _commit(wt, "the push that vanished")
    push_intent.record([_ref(local_sha=second)], repo=str(wt), remote="origin")
    git_in(remote, "update-ref", "refs/heads/main", first)
    push_intent.reconcile()
    err = capsys.readouterr().err
    assert second[:7] in err
    assert first[:7] in err


def test_an_unreachable_remote_is_asked_again_before_it_is_reported(pushable, capsys):
    """"Could not ask" teaches nothing, so it costs a try rather than a report."""
    wt, _ = pushable
    push_intent.record(
        [_ref(local_sha=git_client.head_sha(cwd=wt))], repo=str(wt), remote="origin")
    git_in(wt, "remote", "set-url", "origin", str(wt / "nope.git"))

    for attempt in range(1, push_intent._MAX_ATTEMPTS):
        push_intent.reconcile()
        assert capsys.readouterr().err == ""
        assert [i.attempts for i in _records()] == [attempt]

    push_intent.reconcile()
    assert "could not reach the remote" in capsys.readouterr().err
    assert not push_intent.intents_path().exists()


def test_a_removed_working_tree_drains_without_asking(tmp_path, monkeypatch, capsys):
    """A worktree deleted on purpose takes its unanswered pushes with it."""
    monkeypatch.setattr(push, "remote_head", _never_asked)
    push_intent.record([_ref()], repo=str(tmp_path / "never-existed"), remote="origin")
    push_intent.reconcile()
    assert not push_intent.intents_path().exists()
    assert capsys.readouterr().err == ""


def test_records_are_written_back_before_anything_is_printed(pushable, monkeypatch):
    """A report that dies part way through still leaves the file drained.

    Repeating a report is the failure the whole module is arranged against, and
    an unwritten file is the one thing that would produce it.
    """
    wt, remote = pushable
    push_intent.record(
        [_ref(local_sha=git_client.head_sha(cwd=wt))], repo=str(wt), remote="origin")
    git_in(remote, "update-ref", "-d", "refs/heads/main")

    def _die(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(push_intent, "_report", _die)
    with pytest.raises(KeyboardInterrupt):
        push_intent.reconcile()
    assert not push_intent.intents_path().exists()


def test_every_reportable_outcome_has_a_status_and_no_other_does():
    """A status invented for a drained outcome would describe nobody's push."""
    reportable = {push_intent.Outcome.LOST, push_intent.Outcome.UNANSWERED}
    assert set(push_intent._STATUS) == reportable


# ── the hook's entry point ──────────────────────────────────────────────────


def test_main_records_what_it_reads_on_stdin(monkeypatch):
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(f"refs/heads/foo {'d' * 40} refs/heads/bar {_ZERO}\n"))
    assert push_intent.main(["--repo", "/w", "--remote", "up"]) == 0
    (intent,) = _records()
    assert (intent.branch, intent.remote, intent.sha) == ("bar", "up", "d" * 40)


def test_main_defaults_the_remote_to_origin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"refs/heads/a x refs/heads/a {_ZERO}\n"))
    push_intent.main(["--repo", "/w"])
    assert _records()[0].remote == "origin"


def test_main_reports_a_failure_and_still_exits_zero(monkeypatch, capsys):
    """Non-zero here refuses the push, in every repository on this machine."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"refs/heads/a x refs/heads/a {_ZERO}\n"))
    monkeypatch.setattr(push_intent, "record", _boom)
    assert push_intent.main(["--repo", "/w"]) == 0
    assert "could not record this push" in capsys.readouterr().err


def _boom(*args, **kwargs):
    raise RuntimeError("the bookkeeping broke")


def test_the_state_file_round_trips_through_serde():
    """The hook writes this file and a later `pr` reads it back."""
    push_intent.record([_ref()], repo="/w", remote="origin")
    loaded = serde.load_file(push_intent.IntentFile, push_intent.intents_path())
    assert loaded is not None
    assert loaded.intents == _records()


def test_the_file_lives_under_the_state_root(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "elsewhere"))
    assert push_intent.intents_path() == tmp_path / "elsewhere" / "push-intents.json"


def test_a_push_from_a_worktree_is_recorded_against_that_worktree(pushable):
    """`repo` is where `ls-remote` has to run to reach the same remote."""
    wt, _ = pushable
    assert git_out(wt, "rev-parse", "--show-toplevel").strip()
    push_intent.record([_ref()], repo=str(wt), remote="origin")
    assert _records()[0].repo == str(wt)
