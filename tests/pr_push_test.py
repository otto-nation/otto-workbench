"""Tests for `pr push --expect`: HEAD pushed with a lease, PushDomain recorded, no rebase."""

import sys
from pathlib import Path
from unittest import mock

from batch_git_support import advance, remote_and_clone, remote_tip
from conftest import commit_all, git_in, git_out

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

import cli.pr  # noqa: E402
import cli.pr_push  # noqa: E402
import core.publishing  # noqa: E402
import pr.context  # noqa: E402
import pr.state  # noqa: E402
import rebase.commands  # noqa: E402
import rebase.inspect  # noqa: E402


def _ctx(work):
    return pr.context.resolve_at(pr.context.ContextDepth.LOCAL, repo_dir=str(work))


def _commit(work) -> str:
    (work / "fix.txt").write_text("fix\n")
    commit_all(work, "fix: x")
    return git_out(work, "rev-parse", "HEAD").strip()


def _push(work, expect, **kw) -> int:
    with core.publishing.run(post=True):
        return rebase.commands.cmd_push_head(str(work), _ctx(work), expect=expect, **kw)


def test_head_fast_forwards_onto_the_tip_it_expected(tmp_path):
    pair = remote_and_clone(tmp_path)
    tip = remote_tip(pair.origin, "feat")
    head = _commit(pair.work)
    assert _push(pair.work, tip) == 0
    assert remote_tip(pair.origin, "feat") == head


def test_a_tip_somebody_moved_is_not_overwritten(tmp_path):
    pair = remote_and_clone(tmp_path)
    planned = remote_tip(pair.origin, "feat")
    theirs = advance(pair.seed, "feat", "theirs.txt")
    _commit(pair.work)
    assert _push(pair.work, planned) == 1
    assert remote_tip(pair.origin, "feat") == theirs


def test_a_landed_push_records_where_the_branch_stands_and_no_rebase(tmp_path):
    pair = remote_and_clone(tmp_path)
    tip = remote_tip(pair.origin, "feat")
    _commit(pair.work)
    assert _push(pair.work, tip) == 0
    state = pr.state.load_state(_ctx(pair.work).target_dir)
    assert (state.push.ahead, bool(state.push.updated_at)) == (0, True)
    assert (state.rebase.updated_at, state.rebase.force_pushed) == ("", False)


def test_a_refused_push_records_nothing(tmp_path):
    pair = remote_and_clone(tmp_path)
    planned = remote_tip(pair.origin, "feat")
    advance(pair.seed, "feat", "theirs.txt")
    _commit(pair.work)
    assert _push(pair.work, planned) == 1
    assert pr.state.load_state(_ctx(pair.work).target_dir) is None


def test_with_the_gate_shut_nothing_reaches_the_remote(tmp_path):
    pair = remote_and_clone(tmp_path)
    tip = remote_tip(pair.origin, "feat")
    _commit(pair.work)
    assert rebase.commands.cmd_push_head(str(pair.work), _ctx(pair.work), expect=tip) == 1
    assert remote_tip(pair.origin, "feat") == tip


def test_a_rebase_in_progress_is_refused_before_anything_is_pushed(tmp_path):
    pair = remote_and_clone(tmp_path)
    tip = remote_tip(pair.origin, "feat")
    _commit(pair.work)
    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=True):
        assert _push(pair.work, tip) == 1
    assert remote_tip(pair.origin, "feat") == tip


def test_pr_push_lands_through_the_pr_entry_point(tmp_path):
    pair = remote_and_clone(tmp_path)
    tip = remote_tip(pair.origin, "feat")
    head = _commit(pair.work)
    code = cli.pr.main(["--repo-dir", str(pair.work), "push", "--expect", tip],
                       bin_dir=REPO_ROOT / "ai" / "bin")
    assert code == 0
    assert remote_tip(pair.origin, "feat") == head


def test_pr_push_refuses_a_moved_remote_with_exit_1(tmp_path):
    pair = remote_and_clone(tmp_path)
    planned = remote_tip(pair.origin, "feat")
    theirs = advance(pair.seed, "feat", "theirs.txt")
    _commit(pair.work)
    assert cli.pr_push.main(["--repo-dir", str(pair.work), "--expect", planned]) == 1
    assert remote_tip(pair.origin, "feat") == theirs


@pytest.mark.parametrize("argv", [[], ["--expect", ""]])
def test_pr_push_needs_a_sha_to_lease_on(argv, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.pr_push.main(["--repo-dir", str(tmp_path), *argv])
    assert exc.value.code == 2
    if argv:
        assert "--expect needs the SHA" in capsys.readouterr().err


_REGENERATING_HOOK = """#!/bin/sh
if [ -f .git/regenerated ]; then exit 0; fi
: > .git/regenerated
echo 'regenerated' > gen.txt
echo 'docs are stale — regenerated them' >&2
exit 1
"""


def test_a_regenerating_hook_is_committed_with_the_plain_push_subject(
        tmp_path, live_git_hooks):
    pair = remote_and_clone(tmp_path)
    (pair.work / "gen.txt").write_text("stale\n")
    commit_all(pair.work, "chore: add generated file")
    git_in(pair.work, "push", "-q", "origin", "feat")

    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-push"
    hook.write_text(_REGENERATING_HOOK)
    hook.chmod(0o755)
    git_in(pair.work, "config", "core.hooksPath", str(hooks))

    tip = remote_tip(pair.origin, "feat")
    _commit(pair.work)
    assert _push(pair.work, tip) == 0
    assert git_out(pair.work, "log", "-1", "--pretty=%s").strip() == (
        "chore: regenerate generated files"
    )
    assert remote_tip(pair.origin, "feat") == git_out(
        pair.work, "rev-parse", "HEAD",
    ).strip()
