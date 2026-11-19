import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.events  # noqa: E402
import batch.model  # noqa: E402
import batch.scheduler  # noqa: E402
from batch_scheduler_support import NEED, NO, row  # noqa: E402


def test_new_run_judges_the_rebase_step_by_the_runs_rebase_when():
    only_behind = {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NEED,
                   batch.model.Step.REVIEW: NO}
    rows = [row(1, only_behind, rebase_behind=NEED)]
    steps = list(batch.model.STEP_ORDER)
    default = batch.scheduler.new_run(rows, steps=steps, selected=None, pool=1)
    behind = batch.scheduler.new_run(rows, steps=steps, selected=None, pool=1,
                                     rebase_when=batch.model.RebaseWhen.BEHIND)
    assert default.rebase_when is batch.model.RebaseWhen.CONFLICTS
    assert default.items[0].step(batch.model.Step.REBASE).status is batch.model.StepStatus.SKIPPED
    assert behind.items[0].step(batch.model.Step.REBASE).status is batch.model.StepStatus.PENDING


def test_new_run_stamps_schema_version_3():
    run = batch.scheduler.new_run([row(1)], steps=list(batch.model.STEP_ORDER), selected=None, pool=1)
    assert run.schema_version == 3


def test_new_run_selects_needed_steps_and_drops_rows_with_none():
    rows = [row(1, {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NEED, batch.model.Step.REVIEW: NO}),
            row(2, {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NO, batch.model.Step.REVIEW: NO})]
    run = batch.scheduler.new_run(rows, steps=list(batch.model.STEP_ORDER), selected=None, pool=2)
    assert [i.key for i in run.items] == ["o/r#1"]
    assert [(s.step, s.status) for s in run.items[0].steps] == [
        (batch.model.Step.REBASE, batch.model.StepStatus.SKIPPED),
        (batch.model.Step.CI, batch.model.StepStatus.SKIPPED),
        (batch.model.Step.COMMENTS, batch.model.StepStatus.PENDING),
        (batch.model.Step.REVIEW, batch.model.StepStatus.SKIPPED)]


def test_new_run_honours_explicit_selection():
    run = batch.scheduler.new_run([row(1, {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NO, batch.model.Step.REVIEW: NO})],
                      steps=list(batch.model.STEP_ORDER), selected={"o/r#1": [batch.model.Step.REVIEW]}, pool=1)
    assert run.items[0].step(batch.model.Step.REVIEW).status is batch.model.StepStatus.PENDING


def test_new_run_marks_only_selected_steps_explicit():
    run = batch.scheduler.new_run([row(1)], steps=list(batch.model.STEP_ORDER),
                                  selected={"o/r#1": [batch.model.Step.REVIEW]}, pool=1)
    assert [(s.step, s.explicit) for s in run.items[0].steps] == [
        (batch.model.Step.REBASE, False), (batch.model.Step.CI, False),
        (batch.model.Step.COMMENTS, False), (batch.model.Step.REVIEW, True)]
    planned = batch.scheduler.new_run([row(1)], steps=list(batch.model.STEP_ORDER),
                                      selected=None, pool=1)
    assert not any(s.explicit for s in planned.items[0].steps)


def test_events_are_ndjson_with_schema_version(capsys):
    batch.events.emit("run_started", run="r1")
    line = json.loads(capsys.readouterr().out.strip())
    assert line["schema_version"] == 1 and line["kind"] == "run_started" and line["run"] == "r1"


def test_unknown_event_kind_is_refused():
    with pytest.raises(ValueError):
        batch.events.emit("nope")


NONE_NEEDED = {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NO,
               batch.model.Step.REVIEW: NO}


def test_new_run_keeps_a_row_whose_only_work_is_an_owed_closeout():
    run = batch.scheduler.new_run([row(1, NONE_NEEDED, closeout="summary"), row(2, NONE_NEEDED)],
                                  steps=list(batch.model.STEP_ORDER), selected=None, pool=1)
    assert [i.key for i in run.items] == ["o/r#1"]
    assert all(r.status is batch.model.StepStatus.SKIPPED for r in run.items[0].steps)


def test_new_run_leaves_out_a_closeout_when_comments_is_not_a_chosen_step():
    run = batch.scheduler.new_run([row(1, NONE_NEEDED, closeout="summary")],
                                  steps=[batch.model.Step.REBASE], selected=None, pool=1)
    assert run.items == []


def test_new_run_leaves_out_a_closeout_when_selection_is_explicit():
    run = batch.scheduler.new_run([row(1, NONE_NEEDED, closeout="summary")],
                                  steps=list(batch.model.STEP_ORDER), selected={}, pool=1)
    assert run.items == []
