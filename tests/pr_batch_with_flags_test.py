"""pr batch run --with-ai / --steps / default schema.

Revert: drop each parser check; drop the schema flip in _start_run.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.orchestrate  # noqa: E402
import batch.plan  # noqa: E402
import cli.pr  # noqa: E402
import cli.pr_batch  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402

BIN_DIR = REPO_ROOT / "ai" / "bin"


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _plan_all(monkeypatch):
    rows = [PlanRow("o/r", "/r", 1, "t", "b1", "h", False,
                    {s: StepNeed(True, "x") for s in batch.model.STEP_ORDER}),
            PlanRow("o/r", "/r", 2, "t", "b2", "h", False,
                    {s: StepNeed(True, "x") for s in batch.model.STEP_ORDER})]
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs, prs=None: Plan("me", rows))
    return rows


def _stub_submit(monkeypatch, capture):
    def fake_submit(run, **kwargs):
        capture(run)
        return 0

    monkeypatch.setattr(batch.orchestrate, "submit_ready", fake_submit)


def test_default_run_is_schema_3_without_ai_or_review(monkeypatch):
    _plan_all(monkeypatch)
    seen = {}

    def capture(run):
        seen["steps"] = [s.value for s in run.steps]
        seen["with_ai"] = run.with_ai
        seen["schema"] = run.schema_version

    _stub_submit(monkeypatch, capture)
    assert _main(["batch", "run", "--checkout", "/r"]) == 0
    assert seen["steps"] == ["rebase", "ci", "comments"]
    assert seen["with_ai"] == []
    assert seen["schema"] == 3


def test_steps_review_without_with_review_is_exit_2(monkeypatch, capsys):
    _plan_all(monkeypatch)
    assert _main(["batch", "run", "--checkout", "/r", "--steps", "rebase,review"]) == 2
    assert "review is not in the thin orchestrator yet" in capsys.readouterr().err


def test_steps_rebase_selects_rebase(monkeypatch):
    _plan_all(monkeypatch)
    seen = {}

    def capture(run):
        seen["steps"] = [s.value for s in run.steps]

    _stub_submit(monkeypatch, capture)
    assert _main(["batch", "run", "--checkout", "/r", "--steps", "rebase"]) == 0
    assert seen["steps"] == ["rebase"]


def test_select_rebase_limits_items(monkeypatch):
    _plan_all(monkeypatch)
    seen = {}

    def capture(run):
        seen["steps"] = [s.value for s in run.steps]
        seen["items"] = [(i.key, [s.step.value for s in i.steps
                                  if s.status is batch.model.StepStatus.PENDING])
                         for i in run.items]

    _stub_submit(monkeypatch, capture)
    assert _main(["batch", "run", "--checkout", "/r", "--select", "o/r#2=rebase"]) == 0
    assert "rebase" in seen["steps"]
    assert seen["items"] == [("o/r#2", ["rebase"])]


def test_with_ai_review_is_exit_2(monkeypatch, capsys):
    _plan_all(monkeypatch)
    assert _main(["batch", "run", "--checkout", "/r", "--with-ai=review"]) == 2
    assert "--with-review" in capsys.readouterr().err


def test_auto_publish_ff_is_a_schema_3_run(monkeypatch):
    # Revert: restore type=_steps.
    _plan_all(monkeypatch)
    seen = {}

    def capture(run):
        seen["schema"] = run.schema_version
        seen["with_ai"] = run.with_ai
        seen["ff"] = run.auto_publish_ff

    _stub_submit(monkeypatch, capture)
    assert _main(["batch", "run", "--checkout", "/r", "--auto-publish", "ff"]) == 0
    assert seen["schema"] == 3
    assert seen["with_ai"] == []
    assert seen["ff"] is True


def test_auto_publish_takes_only_ff(monkeypatch, capsys):
    # Revert: restore type=_steps.
    _plan_all(monkeypatch)
    assert _main(["batch", "run", "--checkout", "/r", "--auto-publish", "rebase"]) == 2


def test_resolve_next_and_status_decision_are_gone(capsys):
    # Revert: restore the next subparser (and the resolve subparser / status --decision).
    assert _main(["batch", "resolve", "r1", "d1", "--action", "retry"]) == 2
    err = capsys.readouterr().err
    assert "invalid choice" in err or "invalid" in err.lower() or "unrecognized" in err.lower()
    assert _main(["batch", "status", "r1", "--decision", "d1"]) == 2
    assert _main(["batch", "next", "r1"]) == 2


def test_rebase_defaults_to_conflicts_and_behind_is_persisted_on_the_run(monkeypatch):
    _plan_all(monkeypatch)
    seen = []

    def capture(run):
        seen.append(run.rebase_when)

    _stub_submit(monkeypatch, capture)
    assert _main(["batch", "run", "--checkout", "/r"]) == 0
    assert _main(["batch", "run", "--checkout", "/r", "--rebase", "behind"]) == 0
    assert seen == [batch.model.RebaseWhen.CONFLICTS, batch.model.RebaseWhen.BEHIND]


def test_named_prs_are_planned_by_number_and_a_bare_run_searches(monkeypatch):
    seen = []
    monkeypatch.setattr(batch.plan, "build_run_plan",
                        lambda dirs, prs=None: seen.append(prs) or Plan("me", []))
    _main(["batch", "run", "--checkout", "/r", "--prs", "o/r#1,o/r#2"])
    _main(["batch", "run", "--checkout", "/r"])
    assert seen == [["o/r#1", "o/r#2"], None]


def test_plan_needs_limits_the_steps_judged(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(batch.plan, "build_plan",
                        lambda dirs, steps=None: seen.append(steps) or Plan("me", []))
    assert _main(["batch", "plan", "--checkout", "/r", "--needs", "comments"]) == 0
    assert _main(["batch", "plan", "--checkout", "/r"]) == 0
    assert seen == [frozenset({batch.model.Step.COMMENTS}), None]
