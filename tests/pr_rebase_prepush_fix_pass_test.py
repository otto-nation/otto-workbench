"""Tests for rebase.prepush: the fix pass run on a refused push."""

import contextlib
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.client  # noqa: E402
import git.land  # noqa: E402
import git.regenerate  # noqa: E402
import rebase.prepush  # noqa: E402
import rebase.conflicts  # noqa: E402
import rebase.repo_regen  # noqa: E402
import config.workbench_config  # noqa: E402
import agent.invoke  # noqa: E402
import fix.engine  # noqa: E402
import pr.target  # noqa: E402
import agent.backend  # noqa: E402
import fix.scope  # noqa: E402

from pr_rebase_support import _LEASE


# ── Repo-level regeneration ───────────────────────────────────────────────


@contextlib.contextmanager
def _repo_declaring(commands, *, root, mise_task=False):
    """Stand in for the two sources ``repo_regen.repo_regenerators`` consults."""
    rebase.repo_regen.clear_caches()
    cfg = config.workbench_config.WorkbenchConfig(
        rebase=config.workbench_config.RebaseConfig(regenerate=list(commands)),
    )
    try:
        with mock.patch.object(config.workbench_config, "load_config_or_default",
                               return_value=cfg), \
             mock.patch.object(git.client, "out", return_value=str(root)), \
             mock.patch.object(rebase.repo_regen, "mise_has_task", return_value=mise_task):
            yield
    finally:
        rebase.repo_regen.clear_caches()


# ── the pre-push fix pass ─────────────────────────────────────────────────


def _answering(adapter_box, *, tick="fixed", reason=""):
    """A `run_fix` stub that answers the checklist the engine wrote.

    The engine rewrites the tracking file immediately before each invocation,
    so an answer written any earlier is thrown away before an agent would see
    it. `adapter_box` is a one-element list the caller fills in once the
    adapter exists, since the stub is installed before the pass constructs one.
    """
    def run_fix(_phase, prompt, **_kwargs):
        _prompts.append(prompt)
        tracking = adapter_box[0].tracking_path
        box = f"- [ ] {tick}"
        answer = f"- [x] {tick}" + (f" — {reason}" if reason else "")
        tracking.write_text("\n".join(
            answer if line.startswith(box) else line
            for line in tracking.read_text().splitlines()
        ) + "\n")
        return agent.invoke.FixResult(0, None)
    return run_fix


_prompts: list[str] = []


def _snapshot_reader(snapshots):
    """Answer the baseline once, then the post-agent reading for every read after.

    `None` stays `None` — it is the unreadable worktree, which the engine is
    required to tell apart from an empty set.
    """
    baseline, after = snapshots
    first = iter([set(baseline) if baseline is not None else None])
    rest = set(after) if after is not None else None
    return lambda *_a, **_k: next(first, rest)


@contextlib.contextmanager
def _fix_pass(*, tick="fixed", reason="", landed=None, available=True,
              snapshots=(frozenset(), frozenset({"server.go"}))):
    """Drive the pass with a stubbed agent and a stubbed landing owner.

    `land` is stubbed rather than run: what a landing does belongs to
    `tests/land_test.py`, and what this pass asks for is the assertion here.

    `snapshots` is the dirty set the engine reads before and after the agent;
    stubbed because `tmp_path` is not a repo, and the default says the agent
    touched the one file these tests hand it.

    Given as (baseline, after) and answered by that shape rather than by a list
    of one reply per read: the engine reads the worktree once per batch as well
    as once per pass, so a fixed-length list encodes how many invocations the
    pass makes and fails these tests for a change to batching or retry rather
    than to anything they assert. Every reading after the first answers `after`,
    which describes a worktree the agent edited and then left alone.
    """
    _prompts.clear()
    box: list = [None]
    real_init = rebase.prepush.PrePushFixAdapter.__init__

    def capture(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        box[0] = self

    result = landed or git.land.LandResult(git.land.CommitStatus.PUSHED, "abc1234")
    with mock.patch.object(rebase.prepush.PrePushFixAdapter, "__init__", capture), \
         mock.patch.object(agent.backend, "is_available", return_value=available), \
         mock.patch.object(git.client, "head_sha", return_value="9999999"), \
         mock.patch.object(git.land, "land", return_value=result) as owner, \
         mock.patch.object(fix.scope, "changed_files",
                           side_effect=_snapshot_reader(snapshots)), \
         mock.patch.object(agent.invoke, "run_fix",
                           side_effect=_answering(box, tick=tick, reason=reason)):
        yield owner, box


def test_the_pass_force_pushes_the_branch_it_repaired(tmp_path):
    """A replayed branch's push is non-fast-forward; a plain one is rejected."""
    (tmp_path / "server.go").write_text("package main\n")

    with _fix_pass() as (owner, _):
        result = rebase.prepush.fix_push_failures(
            str(tmp_path), "gofmt: server.go needs formatting", ["server.go"],
            args=_LEASE.args,
        )

    assert result.sha == "abc1234"
    kwargs = owner.call_args.kwargs
    assert kwargs["gated"] is True
    assert kwargs["args"] == _LEASE.args


def test_the_pass_commits_everything_it_touched(tmp_path):
    """Direct agent edits reach the commit instead of being stranded.

    The backend runs with acceptEdits and Bash(*), so a repair can land in a
    file the pass never named — committing only the named files force-pushes
    without the real source fix. The snapshot difference is what covers that:
    it is every file the agent wrote to, named by the check or not.

    What it does not cover is what was already dirty when the pass started.
    That is somebody else's work and was never this commit's to force-push.
    """
    (tmp_path / "server.go").write_text("package main\n")

    with _fix_pass(snapshots=({"theirs.go"},
                              {"theirs.go", "server.go", "unnamed.go"})) as (owner, _):
        rebase.prepush.fix_push_failures(
            str(tmp_path), "vet: server.go", ["server.go"], args=_LEASE.args,
        )

    assert owner.call_args.kwargs["paths"] == {"server.go", "unnamed.go"}


def test_a_pass_that_cannot_say_what_it_touched_commits_nothing(tmp_path):
    """An empty scope commits nothing; None would force-push the whole tree."""
    (tmp_path / "server.go").write_text("package main\n")

    with _fix_pass(snapshots=(frozenset(), None)) as (owner, _):
        rebase.prepush.fix_push_failures(
            str(tmp_path), "vet: server.go", ["server.go"], args=_LEASE.args,
        )

    assert owner.call_args.kwargs["paths"] == set()


def test_the_pass_accounts_for_an_agent_that_committed_its_own_work(tmp_path):
    """Otherwise the pass finds nothing to stage and reports a real fix as nothing."""
    (tmp_path / "server.go").write_text("package main\n")

    with _fix_pass() as (owner, _):
        rebase.prepush.fix_push_failures(
            str(tmp_path), "vet: server.go", ["server.go"], args=_LEASE.args,
        )

    assert owner.call_args.kwargs["recover_from"] == "9999999"


def test_the_commit_body_carries_each_file_s_verdict(tmp_path):
    """Squash-merged with COMMIT_MESSAGES, so this lands verbatim on main."""
    (tmp_path / "server.go").write_text("package main\n")

    with _fix_pass(tick="needs a person", reason="the resolution dropped a branch") as (owner, _):
        rebase.prepush.fix_push_failures(
            str(tmp_path), "vet: server.go", ["server.go"], args=_LEASE.args,
        )

    message = owner.call_args.kwargs["message"]
    assert message.startswith(rebase.prepush.FIX_SUBJECT)
    assert "0 fixed, 1 unresolved" in message
    assert "- server.go — the resolution dropped a branch" in message


def test_the_commit_body_carries_a_fixed_row_s_evidence(tmp_path):
    """A fixed row's evidence lands on main too, not only an unresolved one's.

    The fix box asks what holds the change, and this commit body is the most
    durable place that answer is written down: squash-merged verbatim onto the
    default branch, where it outlives the tracking file and the run's stderr.
    """
    (tmp_path / "server.go").write_text("package main\n")

    with _fix_pass(tick="fixed", reason="golangci-lint run: clean") as (owner, _):
        rebase.prepush.fix_push_failures(
            str(tmp_path), "vet: server.go", ["server.go"], args=_LEASE.args,
        )

    message = owner.call_args.kwargs["message"]
    assert "1 fixed, 0 unresolved" in message
    assert "- server.go — golangci-lint run: clean" in message


def test_a_fixed_row_with_no_evidence_still_names_its_file(tmp_path):
    """The evidence is absent, not the row. An agent that ticks the box and
    says nothing still produced a fix, and the commit body still has to say
    which file it touched.
    """
    (tmp_path / "server.go").write_text("package main\n")

    with _fix_pass(tick="fixed", reason="") as (owner, _):
        rebase.prepush.fix_push_failures(
            str(tmp_path), "vet: server.go", ["server.go"], args=_LEASE.args,
        )

    message = owner.call_args.kwargs["message"]
    assert "- server.go\n" in message or message.rstrip().endswith("- server.go")


def test_the_check_output_reaches_the_agent(tmp_path):
    """The output is the oracle — a pass that withholds it asks for a guess."""
    (tmp_path / "server.go").write_text("package main\n")

    with _fix_pass():
        rebase.prepush.fix_push_failures(
            str(tmp_path), "gofmt: server.go needs formatting", ["server.go"],
            args=_LEASE.args,
        )

    assert "gofmt: server.go needs formatting" in _prompts[0]


def test_the_pass_bills_to_the_run_s_repo_and_pr(tmp_path):
    """A rebase-assist call attributes to the PR the run is rebasing."""
    (tmp_path / "server.go").write_text("package main\n")
    trail = mock.MagicMock()
    trail.context = {"repo": "org/repo", "pr": 7, "branch": "feat/x"}

    with _fix_pass() as (_, box):
        rebase.prepush.fix_push_failures(
            str(tmp_path), "vet: server.go", ["server.go"], args=_LEASE.args,
            trail=trail,
        )

    assert (box[0].repo, box[0].pr, box[0].branch) == ("org/repo", "7", "feat/x")


def test_a_branch_with_no_pr_bills_to_the_repo_alone(tmp_path):
    (tmp_path / "server.go").write_text("package main\n")
    trail = mock.MagicMock()
    trail.context = {"repo": "org/repo", "pr": None, "branch": "feat/x"}

    with _fix_pass() as (_, box):
        rebase.prepush.fix_push_failures(
            str(tmp_path), "vet: server.go", ["server.go"], args=_LEASE.args,
            trail=trail,
        )

    assert (box[0].repo, box[0].pr) == ("org/repo", "")


def test_the_tracking_file_sits_beside_the_rebase_s_other_leavings(tmp_path):
    adapter = rebase.prepush.PrePushFixAdapter(
        str(tmp_path), ["a.py"], "output", args=_LEASE.args,
    )
    artifacts = adapter.artifacts

    assert adapter.tracking_path == artifacts / "fix-tracking.md"
    assert adapter.session_log == artifacts / "fix-session.jsonl"


def test_the_artifacts_are_never_written_into_the_worktree(tmp_path):
    """Including for a checkout with no target key to file under.

    `tmp_path` is not a repo, so `target_dir_for_checkout` returns None — the
    detached-HEAD and no-origin case. Falling back to the worktree there would
    reintroduce the whole defect for the one case nothing else covers, and the
    scoped commit would then sweep the pass's own bookkeeping in: these files
    are written before the post-agent snapshot, so they read as the agent's.
    """
    worktree = tmp_path / "wt"
    worktree.mkdir()
    adapter = rebase.prepush.PrePushFixAdapter(
        str(worktree), ["a.py"], "output", args=_LEASE.args,
    )

    assert worktree not in adapter.artifacts.parents
    assert adapter.artifacts.is_relative_to(pr.target.targets_root())


def test_two_unkeyed_checkouts_do_not_share_a_directory(tmp_path):
    """The digest is what keeps one checkout's pass out of another's."""
    one = tmp_path / "wt"
    two = tmp_path / "other" / "wt"
    two.mkdir(parents=True)
    one.mkdir()

    assert (rebase.prepush.PrePushFixAdapter(str(one), ["a.py"], "o", args=_LEASE.args).artifacts
            != rebase.prepush.PrePushFixAdapter(str(two), ["a.py"], "o", args=_LEASE.args).artifacts)


def test_the_pass_records_what_it_did_on_the_trail(tmp_path):
    """The trail is the only durable record — no ctx here means no FixRecord."""
    (tmp_path / "server.go").write_text("package main\n")
    trail = mock.MagicMock()
    trail.context = {}

    with _fix_pass():
        rebase.prepush.fix_push_failures(
            str(tmp_path), "vet: server.go", ["server.go"], args=_LEASE.args,
            trail=trail,
        )

    entries = {c.args[1]: c.kwargs.get("data", {}) for c in trail.info.call_args_list}
    assert "committed check-failure fixes" in entries
    assert entries["committed check-failure fixes"]["files"] == ["server.go"]
    assert entries["committed check-failure fixes"]["sha"] == "abc1234"


def test_a_pass_that_committed_nothing_is_not_reported_as_a_commit(tmp_path):
    """Otherwise the trail carries a fix no SHA stands behind."""
    (tmp_path / "server.go").write_text("package main\n")
    trail = mock.MagicMock()
    trail.context = {}

    with _fix_pass(landed=git.land.LandResult(git.land.CommitStatus.NO_CHANGES)):
        rebase.prepush.fix_push_failures(
            str(tmp_path), "vet: server.go", ["server.go"], args=_LEASE.args,
            trail=trail,
        )

    entries = [c.args[1] for c in trail.info.call_args_list]
    assert "pre-push fix pass committed nothing" in entries
    assert "committed check-failure fixes" not in entries


def test_no_backend_attempts_nothing(tmp_path):
    """The engine invokes unconditionally, so the guard stays outside it."""
    (tmp_path / "file.go").write_text("package main\n")

    with _fix_pass(available=False) as (owner, _), \
         mock.patch.object(rebase.conflicts, "is_generated_file", return_value=None):
        result = rebase.prepush.fix_push_failures(
            str(tmp_path), "errors", ["file.go"], args=_LEASE.args,
        )

    assert result is None
    owner.assert_not_called()


# ── generated files are rebuilt, never edited ─────────────────────────────


def test_a_generated_file_is_rebuilt_instead_of_prompted(tmp_path):
    """The bug this guard exists for: a generated file must never reach the AI.

    Hand-editing a protobuf descriptor or a hash manifest cannot produce the
    generator's output, so the drift check that refused the push refuses it
    again — after spending the agent's budget per artifact.
    """
    (tmp_path / "models.go").write_text("// Code generated by sqlc. DO NOT EDIT.\n")
    regenerated = []

    with _fix_pass() as (owner, box), \
         mock.patch.object(git.regenerate, "run_regeneration",
                           side_effect=lambda job, **kw: regenerated.append(job.cmd) or True), \
         _repo_declaring(["mise run generate"], root=tmp_path):
        rebase.prepush.fix_push_failures(
            str(tmp_path), "drift: models.go", ["models.go"], args=_LEASE.args,
        )

    assert box[0] is None, "a generated file was handed to the fix pass"
    assert regenerated == [("mise", "run", "generate")]


def test_a_rebuild_with_nothing_else_to_fix_is_still_landed(tmp_path):
    """The engine no-ops on an empty item set, but the rebuild is the repair.

    Withholding it because no editable file was named leaves the branch
    unpushable for the same drift the hook rejected.
    """
    (tmp_path / "models.go").write_text("// Code generated by sqlc. DO NOT EDIT.\n")

    with _fix_pass(), \
         mock.patch.object(git.regenerate, "run_regeneration", return_value=True), \
         mock.patch.object(git.land, "land",
                           return_value=git.land.LandResult(git.land.CommitStatus.PUSHED, "reb1234")) as owner, \
         _repo_declaring(["mise run generate"], root=tmp_path):
        result = rebase.prepush.fix_push_failures(
            str(tmp_path), "drift: models.go", ["models.go"], args=_LEASE.args,
        )

    assert result.sha == "reb1234"
    kwargs = owner.call_args.kwargs
    assert kwargs["args"] == _LEASE.args
    assert kwargs["gated"] is True
    assert kwargs["message"] == rebase.prepush.REGEN_MESSAGE
    # No agent ran, so there is no snapshot to scope from and none is needed:
    # a regeneration's output is known by name.
    assert kwargs["paths"] == ["models.go"]


def test_a_rebuild_alongside_editable_work_is_swept_into_the_agent_s_commit(tmp_path):
    """One commit, not two: a hook validates the worktree, not the commits under it.

    Splitting the rebuild out would push a HEAD the green run never saw.
    """
    (tmp_path / "models.go").write_text("// Code generated by sqlc. DO NOT EDIT.\n")
    (tmp_path / "server.go").write_text("package main\n")

    with _fix_pass() as (owner, _), \
         mock.patch.object(git.regenerate, "run_regeneration", return_value=True), \
         _repo_declaring(["mise run generate"], root=tmp_path):
        rebase.prepush.fix_push_failures(
            str(tmp_path), "drift: models.go, vet: server.go",
            ["models.go", "server.go"], args=_LEASE.args,
        )

    # `prepush.land` and `fix.engine.land` are one module, so the count is what
    # distinguishes one commit from two — not which name the owner was reached
    # through.
    assert owner.call_count == 1
    assert owner.call_args.kwargs["message"].startswith(rebase.prepush.FIX_SUBJECT)
    # The rebuild ran before the pass, so `models.go` was already dirty when
    # the engine took its baseline and is not in the agent's delta. It is in
    # the scope regardless, because it belongs to this one commit.
    assert owner.call_args.kwargs["paths"] == {"models.go", "server.go"}


def test_the_agent_is_asked_only_about_the_hand_written_files(tmp_path):
    """The guard is scoped: a normal file alongside a generated one still gets fixed."""
    (tmp_path / "models.go").write_text("// Code generated by sqlc. DO NOT EDIT.\n")
    (tmp_path / "handler.go").write_text("package main\n")

    with _fix_pass() as (_, box), \
         mock.patch.object(git.regenerate, "run_regeneration", return_value=True), \
         _repo_declaring(["mise run generate"], root=tmp_path):
        rebase.prepush.fix_push_failures(
            str(tmp_path), "drift: models.go, vet: handler.go",
            ["models.go", "handler.go"], args=_LEASE.args,
        )

    assert [i.id for i in box[0].items()] == ["handler.go"]


def test_a_generated_file_with_no_regenerator_is_left_alone(tmp_path):
    """Unable to rebuild is still not a licence to hand-edit."""
    (tmp_path / "x.gen").write_text("// @generated\n")

    with _fix_pass() as (owner, box), \
         _repo_declaring([], root=tmp_path, mise_task=False):
        result = rebase.prepush.fix_push_failures(
            str(tmp_path), "drift: x.gen", ["x.gen"], args=_LEASE.args,
        )

    assert box[0] is None
    assert result is None
    owner.assert_not_called()


def test_only_the_files_actually_rebuilt_are_reported_as_committed(tmp_path):
    """A generated file nobody could rebuild is not part of the commit.

    `excluded` holds every file held back from the agent, rebuildable or not.
    Reporting that list would put an unchanged file in the trail entry
    `otto-log query` reads as the record of what the commit carried.
    """
    (tmp_path / "models.go").write_text("// Code generated by sqlc. DO NOT EDIT.\n")
    (tmp_path / "x.gen").write_text("// @generated\n")
    trail = mock.MagicMock()
    trail.context = {}

    def only_models(filepath, cwd, queue):
        if filepath != "models.go":
            return False
        queue.add(Path(cwd), filepath, ("mise", "run", "generate"))
        return True

    with _fix_pass(), \
         mock.patch.object(rebase.prepush.repo_regen, "queue_repo_regeneration",
                           side_effect=only_models), \
         mock.patch.object(git.regenerate, "run_regeneration", return_value=True), \
         mock.patch.object(git.land, "land",
                           return_value=git.land.LandResult(git.land.CommitStatus.PUSHED, "reb1234")):
        rebase.prepush.fix_push_failures(
            str(tmp_path), "drift: models.go, drift: x.gen",
            ["models.go", "x.gen"], args=_LEASE.args, trail=trail,
        )

    entries = {c.args[1]: c.kwargs.get("data", {}) for c in trail.info.call_args_list}
    assert entries["committed regenerated files"]["files"] == ["models.go"]


def test_a_rebuild_that_did_not_commit_is_recorded(tmp_path):
    """A regeneration that lands nothing is the quiet failure worth a trail entry."""
    (tmp_path / "models.go").write_text("// Code generated by sqlc. DO NOT EDIT.\n")
    trail = mock.MagicMock()
    trail.context = {}

    with _fix_pass(), \
         mock.patch.object(git.regenerate, "run_regeneration", return_value=True), \
         mock.patch.object(git.land, "land",
                           return_value=git.land.LandResult(git.land.CommitStatus.NO_CHANGES)), \
         _repo_declaring(["mise run generate"], root=tmp_path):
        rebase.prepush.fix_push_failures(
            str(tmp_path), "drift: models.go", ["models.go"],
            args=_LEASE.args, trail=trail,
        )

    assert "regenerated files did not commit" in [
        c.args[1] for c in trail.error.call_args_list
    ]


# ── which files the pass is asked about ───────────────────────────────────


def _targets(tmp_path, error_output, resolved_files):
    """The files the pass would hand an agent, without running one."""
    box: list = [None]
    real_init = rebase.prepush.PrePushFixAdapter.__init__

    def capture(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        box[0] = self

    for name in dict.fromkeys(resolved_files):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stub\n")

    with mock.patch.object(rebase.prepush.PrePushFixAdapter, "__init__", capture), \
         mock.patch.object(agent.backend, "is_available", return_value=True), \
         mock.patch.object(fix.engine, "run", return_value=fix.engine.FixRun()):
        rebase.prepush.fix_push_failures(
            tmp_path.as_posix(), error_output, resolved_files, args=_LEASE.args,
        )

    return box[0].editable if box[0] else []


def test_only_the_files_the_check_names_are_handed_over(tmp_path):
    """A rebase resolves dozens of files; the pass asks about the named ones.

    Handing over every resolved file spends budget per entry and gives an agent
    with edit access a file the check never complained about.
    """
    resolved = ["bin/otto-workbench", "docs/libraries.md", "ai/lib/prompt.py"]
    error = "✗ bin/otto-workbench: nesting exceeds 2 levels\n      line 1304: depth 3\n"

    assert _targets(tmp_path, error, resolved) == ["bin/otto-workbench"]


def test_a_file_resolved_in_several_commits_is_handed_over_once(tmp_path):
    resolved = ["ai/lib/review_issue.py"] * 4
    error = "ai/lib/review_issue.py:12: undefined name"

    assert _targets(tmp_path, error, resolved) == ["ai/lib/review_issue.py"]


def test_a_basename_only_report_still_scopes_to_that_file(tmp_path):
    resolved = ["pkg/server.go", "pkg/client.go"]
    error = "gofmt: server.go needs formatting"

    assert _targets(tmp_path, error, resolved) == ["pkg/server.go"]


def test_check_output_naming_no_file_leaves_every_file_a_suspect(tmp_path):
    resolved = ["pkg/server.go", "pkg/client.go"]

    assert _targets(tmp_path, "build failed: exit status 2", resolved) == resolved


def test_the_unscoped_fallback_is_recorded(tmp_path):
    """The expensive unscoped pass is visible in the trail, not just in spend."""
    (tmp_path / "server.go").write_text("stub\n")
    trail = mock.MagicMock()
    trail.context = {}

    with _fix_pass():
        rebase.prepush.fix_push_failures(
            str(tmp_path), "build failed", ["server.go"],
            args=_LEASE.args, trail=trail,
        )

    assert "check output named no resolved file" in [
        c.args[1] for c in trail.info.call_args_list
    ]


def test_the_error_output_handed_over_is_truncated(tmp_path):
    """A check can print megabytes; the prompt carries a bounded slice of it."""
    (tmp_path / "server.go").write_text("package main\n")
    long_error = "server.go " + "x" * 10000

    with _fix_pass() as (_, box):
        rebase.prepush.fix_push_failures(
            str(tmp_path), long_error, ["server.go"], args=_LEASE.args,
        )

    assert len(box[0].check_output) == rebase.prepush.FIX_ERROR_MAX_CHARS
