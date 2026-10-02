"""Tests for the review entry point — PR refs, the generator version, the parser,
--recover with --self, and recording the review on the PR's state."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
import core.proc
import core.version
import pr.context
from pr.domains import ReviewVerdict
import review.completion
import review.gc
import review.invoke
import review.issue
import review.preflight
import review.publish
import review.run
import review.worktree

from conftest import make_ctx, run_checked

import core.prompt
import gh.client

# cr and reviews_dir are pytest fixtures: imported by name so tests here can request them.
from review_entry_support import cr, reviews_dir, _self_flags, _stub_pr_flow


# ── is_pr_ref ─────────────────────────────────────────────────────────────────


def test_is_pr_ref_bare_number():
    assert pr.context.is_pr_ref("42") is True


def test_is_pr_ref_github_url():
    assert pr.context.is_pr_ref("https://github.com/org/repo/pull/123") is True


def test_is_pr_ref_branch_name():
    assert pr.context.is_pr_ref("isaac/feat/dream_scripts") is False


def test_is_pr_ref_branch_with_numbers():
    assert pr.context.is_pr_ref("isaac/fix/PR-123-review") is False


def test_is_pr_ref_empty():
    assert pr.context.is_pr_ref("") is False


# ── _generator_version ────────────────────────────────────────────────────────


def test_the_generator_version_is_resolved_not_injected(cr, reviews_dir, monkeypatch):
    """Every entry point records the same generator, with no caller's help.

    It used to be a parameter, and the in-process caller — `pr review`, which
    is how the command is normally reached — passed nothing. Its reviews all
    recorded `generator: … unknown` while the `ai/bin` shim recorded the real
    build: one fact with two spellings, decided by the entry point.
    """
    seen = {}
    monkeypatch.setattr(cr, "_run_self_review",
                        lambda args, argv, gv: seen.setdefault("gv", gv))
    monkeypatch.setattr(core.version, "tool_version", lambda: "9.9.9")
    monkeypatch.setattr(core.version, "workbench_version", lambda: "1.0 (abc)")

    cr.main(["--self"])

    assert seen["gv"] == f"{cr.SCRIPT} 9.9.9 / otto-workbench 1.0 (abc)", (
        "the marker names the tool and both versions behind it, on one line"
    )


def test_the_generator_version_names_the_tool_even_with_no_workbench_manifest(
    cr, reviews_dir, monkeypatch,
):
    """A packaged install has a tool version and no workbench release.

    The earlier form took the *last* line of the two-line `--version` output,
    so where there was no second line it recorded the tool version and where
    there was one it recorded the workbench build instead of the tool — the
    marker named a different thing depending on the layout it ran from.
    """
    seen = {}
    monkeypatch.setattr(cr, "_run_self_review",
                        lambda args, argv, gv: seen.setdefault("gv", gv))
    monkeypatch.setattr(core.version, "tool_version", lambda: "9.9.9")
    monkeypatch.setattr(core.version, "workbench_version", lambda: "")

    cr.main(["--self"])

    assert seen["gv"] == f"{cr.SCRIPT} 9.9.9"


# ── CLI argument parsing ──────────────────────────────────────────────────────
#
# Parsing is covered in review_flow_entry_test.py, against review's own
# parser. The two tests that stood here built a throwaway ArgumentParser and
# asserted that argparse works, so every flag they named could have been
# renamed or dropped with this suite green.


# ── Constants ─────────────────────────────────────────────────────────────────


def test_constants_match_expected(cr):
    from review.types import SEVERITIES
    assert review.gc.GC_STALE_DAYS == 7
    assert review.gc.PRUNE_MAX_FILES == 10
    assert len(SEVERITIES) == 4


def test_max_parallel_defaults_to_derived_capacity(cr):
    args = cr.build_parser().parse_args(["42"])
    assert args.max_parallel is None


def test_build_parser_does_not_read_the_process_argv(cr, monkeypatch):
    """Building a parser must not be able to end the process that asked for it.

    `pr` calls this to read flag arity while classifying its own argv, so a
    factory that inspects `sys.argv` and exits would take `pr` with it.
    """
    monkeypatch.setattr(sys, "argv", ["review", "--value-flags", "42"])
    assert cr.build_parser().parse_args(["42"]).pr is None


def _write_partial_pipeline(review_dir: Path, head_sha: str = "abc1234") -> None:
    (review_dir / "pipeline.json").write_text(json.dumps({
        "head_sha": head_sha, "group_names": ["g1", "g2"],
        "failed": {"synthesis": "crashed"},
        "groups_done": [1], "groups_failed": {},
    }))


# ── --recover with --self ─────────────────────────────────────────────────────


def test_self_review_accepts_recover(cr, reviews_dir, monkeypatch):
    """--recover is a top-level mode; --self must not reject it."""
    run_self = MagicMock()
    monkeypatch.setattr(cr, "_run_self_review", run_self)

    cr.main(["--self", "--recover"])

    assert run_self.call_count == 1
    assert run_self.call_args[0][0].recover is True


def test_self_review_recover_reads_head_after_worktree_switch(
    cr, tmp_path, reviews_dir, monkeypatch,
):
    """Checking out the target moves HEAD — the recover sha must come from the new worktree."""
    ctx = make_ctx(
        repo="owner/repo", pr_number=None, branch="feat/x", head_sha="stale00",
        worktree_root=tmp_path, target_dir=tmp_path / "pr" / "owner-repo-x-feat-x",
    )
    monkeypatch.setattr(review.worktree, "resolve_wt_path", lambda repo_dir, pr_input: "/orig/wt")
    monkeypatch.setattr(review.worktree, "resolve_branch_input", lambda pr_input, repo_dir: pr_input)
    monkeypatch.setattr(pr.context, "resolve_at", lambda depth, **kw: ctx)
    monkeypatch.setattr(pr.context, "pr_number_if_reachable",
                        lambda repo, branch: pr.context.BranchPR())
    monkeypatch.setattr(
        review.worktree, "switch_to_branch",
        lambda branch, wt: review.worktree.WorktreeResult(
            path="/switched/wt", cleanup_ref=branch, is_fallback=False),
    )
    monkeypatch.setattr(
        pr.context, "head_sha",
        lambda cwd=None: "fresh11" if cwd == "/switched/wt" else "stale00",
    )
    monkeypatch.setattr(review.worktree, "cleanup_self_review_worktree", lambda *a, **kw: None)
    body = MagicMock()
    monkeypatch.setattr(review.run, "run_self_review", body)

    cr._run_self_review(SimpleNamespace(
        positional=["feat/x"], issue=None, max_parallel=1, skip_user_verification=True,
        force=False, no_holistic=False, no_scout=False, disprove=None, max_cost=None,
        model=None, repo_dir="", fix=False, effort="medium", max_groups=None,
        generated=False, recover=True, debug=False, base="",
        post=False, push=False, no_post=False, submit=False,
    ), [], "test 1.0")

    assert body.call_args.kwargs["recover_head_sha"] == "fresh11"


def test_pr_review_reads_the_tracker_from_the_repo_config(tmp_path, monkeypatch):
    """The PR path resolves the provider against the clone, like --self does.

    Called with no path it reads the machine config only, so a repo that
    declares its tracker in .workbench.yml would review as if none were set.
    """
    (tmp_path / ".workbench.yml").write_text(
        "issues:\n  provider: github\n",
    )
    monkeypatch.setattr(review.worktree, "find_repo_root", lambda repo, repo_dir="": str(tmp_path))
    # Only the PR lookup is stubbed — the config reader still shells out to yq.
    monkeypatch.setattr(gh.client, "pr_view", lambda *a, **kw: {})
    seen = []

    def _record(provider, *args):
        seen.append(provider)
        raise SystemExit(7)

    monkeypatch.setattr(review.issue, "extract_issue_id", _record)

    with pytest.raises(SystemExit) as exc:
        review.run.run_pr_review(
            make_ctx(),
            review.run.ReviewFlags(bin_dir=Path("/bin"), generator_version="test 1.0",
                                   no_post=True),
            tmp_path / "review.md", trail=MagicMock(),
        )

    assert exc.value.code == 7
    assert seen == ["github"]


def _self_ctx(tmp_path, branch="feat/x"):
    """The identity a --self run resolves once and threads down."""
    return make_ctx(
        repo="owner/repo", pr_number=None, branch=branch, head_sha="abc1234",
        worktree_root=tmp_path, target_dir=tmp_path / "state",
    )


def test_self_review_body_validates_recover(tmp_path, monkeypatch):
    """recover=True reaches resolve_recover_sha — no pipeline state aborts the run."""
    monkeypatch.setattr(review.preflight, "refuse_if_superseded", lambda *a, **kw: None)
    with pytest.raises(SystemExit) as exc:
        review.run.run_self_review(
            _self_ctx(tmp_path), _self_flags(recover=True),
            tmp_path, str(tmp_path),
            recover_head_sha="abc1234", trail=MagicMock(),
        )
    assert exc.value.code == 1


def test_self_review_body_runs_recover_in_pinned_worktree(tmp_path, monkeypatch):
    """New commits since the failed run: orchestrate runs against the pinned checkout."""
    _write_partial_pipeline(tmp_path)
    monkeypatch.setattr(review.preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(pr.context, "head_sha", lambda cwd=None: "def5678")
    pinned = review.worktree.WorktreeResult(
        path="/pinned/wt", cleanup_ref="/pinned/wt", is_fallback=True)
    monkeypatch.setattr(
        review.worktree, "detached_worktree_at",
        lambda sha, repo_dir, label: pinned,
    )
    cleanup = MagicMock()
    monkeypatch.setattr(review.worktree, "cleanup_worktree", cleanup)
    monkeypatch.setattr(review.issue, "load_issue_provider",
                        lambda wt: SimpleNamespace(name="none", options={}))
    monkeypatch.setattr(review.issue, "extract_issue_id", lambda *a: "")
    monkeypatch.setattr(review.issue, "fetch_issue_context",
                        lambda *a: SimpleNamespace(link="", context=""))
    run = MagicMock(side_effect=SystemExit(1))
    monkeypatch.setattr(review.invoke, "run", run)

    with pytest.raises(SystemExit):
        review.run.run_self_review(
            _self_ctx(tmp_path), _self_flags(recover=True),
            tmp_path, str(tmp_path),
            recover_head_sha="def5678", trail=MagicMock(),
        )

    request = run.call_args[0][0]
    assert request.wt_path == "/pinned/wt"
    assert request.recover_sha == "abc1234"
    assert cleanup.call_args[0][0] is pinned


def test_self_review_body_rejects_fix_on_drifted_recover(tmp_path, monkeypatch):
    """Fixes written to a throwaway checkout would be discarded — refuse up front."""
    _write_partial_pipeline(tmp_path)
    monkeypatch.setattr(review.preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(pr.context, "head_sha", lambda cwd=None: "def5678")
    monkeypatch.setattr(review.issue, "load_issue_provider",
                        lambda wt: SimpleNamespace(name="none", options={}))
    monkeypatch.setattr(review.issue, "extract_issue_id", lambda *a: "")
    monkeypatch.setattr(review.issue, "fetch_issue_context",
                        lambda *a: SimpleNamespace(link="", context=""))

    with pytest.raises(SystemExit) as exc:
        review.run.run_self_review(
            _self_ctx(tmp_path), _self_flags(recover=True, fix=True),
            tmp_path, str(tmp_path),
            recover_head_sha="def5678", trail=MagicMock(),
        )
    assert exc.value.code == 1


def test_self_review_body_allows_fix_when_recover_has_not_drifted(tmp_path, monkeypatch):
    """No drift means no throwaway checkout, so --fix edits the real worktree."""
    _write_partial_pipeline(tmp_path)
    monkeypatch.setattr(review.preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(pr.context, "head_sha", lambda cwd=None: "abc1234")
    detach = MagicMock()
    monkeypatch.setattr(review.worktree, "detached_worktree_at", detach)
    monkeypatch.setattr(review.worktree, "cleanup_worktree", MagicMock())
    monkeypatch.setattr(review.issue, "load_issue_provider",
                        lambda wt: SimpleNamespace(name="none", options={}))
    monkeypatch.setattr(review.issue, "extract_issue_id", lambda *a: "")
    monkeypatch.setattr(review.issue, "fetch_issue_context",
                        lambda *a: SimpleNamespace(link="", context=""))
    run = MagicMock(side_effect=SystemExit(1))
    monkeypatch.setattr(review.invoke, "run", run)

    with pytest.raises(SystemExit):
        review.run.run_self_review(
            _self_ctx(tmp_path), _self_flags(recover=True, fix=True),
            tmp_path, str(tmp_path),
            recover_head_sha="abc1234", trail=MagicMock(),
        )

    request = run.call_args[0][0]
    assert request.wt_path == str(tmp_path)
    assert request.fix_pass is True
    assert detach.call_count == 0


# ── record_domain ────────────────────────────────────────────────────────────


def _caller_checkout(path: Path, branch: str = "main") -> Path:
    """A real checkout with an origin and a commit, standing in for the caller."""
    path.mkdir(parents=True)
    run_checked(["git", "init", "-q", "-b", branch, str(path)])
    run_checked(["git", "-C", str(path), "remote", "add", "origin",
                 "git@github.com:acme/widget.git"])
    run_checked(
        ["git", "-C", str(path), "commit", "-q", "--allow-empty", "-m", "x"],
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return path


def test_a_review_that_produced_no_file_is_not_recorded(tmp_path, monkeypatch):
    """No review on disk means no verdict to record, and no state write.

    The guard exists because `record_domain` runs from the shared tail, which a
    caller can reach on paths where the review file is absent. Writing anyway
    would stamp the domain with an all-zero report — `build_review_summary`
    tolerates a missing file and returns exactly that — so `pr status` would
    show a clean review for a run that produced nothing.
    """
    wrote = MagicMock()
    monkeypatch.setattr(review.completion, "sync_review_domain", wrote)

    review.completion.record_domain(
        make_ctx(), tmp_path / "nonexistent" / "review.md", trail=MagicMock(),
    )

    wrote.assert_not_called()


def test_update_pr_state_writes_to_the_prs_target_not_the_callers(
    cr, tmp_path, monkeypatch,
):
    """The summary belongs to the PR reviewed, not to where we stood.

    `pr review <N>` is routinely run from a checkout sitting on some other
    branch. The run's identity is resolved once at entry and threaded down; a
    second resolution here reads the caller's branch instead, which is how the
    verdict for one PR used to overwrite another's state.

    Both targets are built through `pr.target` — the repo key is opaque and no
    test may reconstruct one.
    """
    import pr.state
    import pr.target

    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    caller = _caller_checkout(tmp_path / "caller", branch="main")
    repo_key = pr.target.repo_key_from_origin(str(caller))
    callers_target = pr.target.target_dir(repo_key, "main")
    prs_target = pr.target.target_dir(repo_key, "feat/x")

    review_dir = tmp_path / "review"
    review_dir.mkdir()
    review_file = review_dir / "review.md"
    review_file.write_text("## Must fix\n- **[M1]** boom\n")

    def _refuse(**kwargs):
        raise AssertionError("the run's identity is resolved once, at entry")

    monkeypatch.setattr(pr.context, "resolve", _refuse)
    monkeypatch.chdir(caller)

    ctx = make_ctx(repo="acme/widget", branch="feat/x", pr_number=2973,
                   worktree_root=caller, head_sha="deadbee",
                   target_dir=prs_target)
    review.completion.record_domain(ctx, review_file, trail=MagicMock())

    state = pr.state.load_state(prs_target)
    assert state is not None, "summary did not land with the PR under review"
    assert state.review.finding_counts == {"must_fix": 1}
    assert state.review.verdict == ReviewVerdict.CHANGES_REQUESTED.value
    assert state.identity.pr_number == 2973
    assert state.identity.branch == "feat/x"
    assert not (callers_target / pr.state.STATE_FILE).exists()


def test_an_unsatisfying_review_is_still_recorded(tmp_path, monkeypatch):
    """Declining to post is not declining to record.

    This branch used to end in `sys.exit(0)`, which skipped the caller's
    `_update_pr_state` while its three siblings returned into it. The exit was
    written when `cmd_review` was the last thing `main` did and exiting was the
    same as returning; the state write arrived two months later and was never
    reconciled with it. The result was a review that had run, cost money and
    found must-fixes being reported by `pr status` as "not checked: review" —
    the one distinction `Readiness` exists to preserve.
    """
    review_file = tmp_path / "review" / "review.md"
    review_file.parent.mkdir()
    review_file.write_text("## Must fix\n- **[M1]** boom\n")

    _stub_pr_flow(monkeypatch, tmp_path)
    # False is "not satisfied" — the branch under test.
    monkeypatch.setattr(core.prompt, "confirm", lambda *a, **kw: False)
    posted = MagicMock()
    monkeypatch.setattr(review.publish, "post", posted)
    monkeypatch.setattr(core.prompt, "ask", lambda *a, **kw: "")

    review.run.run_pr_review(
        make_ctx(target_dir=tmp_path / "t"),
        review.run.ReviewFlags(bin_dir=Path("/bin"), generator_version="test 1.0"),
        review_file, trail=MagicMock(),
    )

    posted.assert_not_called()


def test_an_unsatisfying_review_does_not_exit_the_process(tmp_path, monkeypatch):
    """The caller must get control back, which is what makes the write reachable.

    Asserting the absence of `SystemExit` is the whole point: a `sys.exit(0)`
    here reports success to the shell while skipping everything the caller does
    after the body, and both the domain write and the `--json-summary`
    emission live there.
    """
    review_file = tmp_path / "review" / "review.md"
    review_file.parent.mkdir()
    review_file.write_text("## Must fix\n- **[M1]** boom\n")

    _stub_pr_flow(monkeypatch, tmp_path)
    monkeypatch.setattr(core.prompt, "confirm", lambda *a, **kw: False)
    monkeypatch.setattr(core.prompt, "ask", lambda *a, **kw: "")

    try:
        review.run.run_pr_review(
            make_ctx(target_dir=tmp_path / "t"),
            review.run.ReviewFlags(bin_dir=Path("/bin"), generator_version="test 1.0"),
            review_file, trail=MagicMock(),
        )
    except SystemExit as exc:  # pragma: no cover - the regression itself
        pytest.fail(f"body exited instead of returning (code {exc.code}); "
                    "the caller's state write is unreachable")


def test_update_pr_state_reports_a_failed_write_on_both_channels(
    tmp_path, monkeypatch, capsys,
):
    """A lost summary is a cache miss, not a failed review — but never silent."""
    import pr.state

    review_file = tmp_path / "review.md"
    review_file.write_text("## Findings\n")

    def _boom(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(pr.state, "save_state", _boom)
    trail = MagicMock()
    ctx = make_ctx(repo="acme/widget", branch="feat/x", pr_number=1,
                   worktree_root=None, head_sha="deadbee",
                   target_dir=tmp_path / "target")

    review.completion.record_domain(ctx, review_file, trail=trail)

    assert "read-only file system" in trail.error.call_args[0][1]
    assert "read-only file system" in capsys.readouterr().err


def test_an_in_process_caller_can_keep_its_own_signal_handler(cr, monkeypatch):
    """The entry point that owns the process owns the stop signals.

    `signal.signal` overwrites without chaining and nothing restores it, so a
    `main` called in-process must be able to decline to install one rather than
    silently replacing its caller's for the rest of the run.
    """
    import signal
    from unittest.mock import patch

    installed = []
    monkeypatch.setattr(signal, "signal", lambda *a: installed.append(a[0]))

    def signals_installed_by(flag):
        installed.clear()
        with pytest.raises(RuntimeError):
            cr.main([], install_signal_handler=flag)
        return list(installed)

    with patch.object(cr, "build_parser", side_effect=RuntimeError("stop")):
        assert signals_installed_by(False) == []
        assert signals_installed_by(True) == list(core.proc.STOP_SIGNALS)
