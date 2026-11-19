import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from batch.model import Step  # noqa: E402
from batch.orchestrate import submit_ready  # noqa: E402
from batch.scheduler import new_run  # noqa: E402
from batch_scheduler_support import NEED, row  # noqa: E402
from otto_queue.model import EntryClass, EntryState  # noqa: E402
from otto_queue.submit import SubmitOutcome, SubmitResult  # noqa: E402

REBASE_NEED = {Step.REBASE: NEED}


class FakeSubmit:
    def __init__(self):
        self.calls = []
        self.n = 0
        self.busy_after = None

    def __call__(self, req):
        self.n += 1
        if self.busy_after == self.n:
            return SubmitResult(75, SubmitOutcome.BUSY)
        self.calls.append(req)
        return SubmitResult(0, SubmitOutcome.QUEUED, self.n)


def _ensure(item):
    item.worktree = f"/wt/{item.branch}"
    return ""


def _submit(run, submit, *, states=None, wanted=None, ensure_worktree=_ensure):
    return submit_ready(
        run,
        pr_bin="pr",
        now="t",
        submit=submit,
        states=states or (lambda ids: {}),
        dirty=lambda wt: False,
        rebasing=lambda wt: False,
        branch_ok=lambda item: True,
        wanted=wanted or (lambda item, step: step is Step.REBASE),
        conflict_files=lambda item: (),
        generated=lambda _item, p: False,
        ensure_worktree=ensure_worktree,
    )


def _rebase_run(*rows):
    return new_run(list(rows), steps=[Step.REBASE], selected=None, pool=1)


def test_submit_ready_records_entry_ids_and_exits_without_waiting():
    run = _rebase_run(row(1, REBASE_NEED))
    fake = FakeSubmit()
    assert _submit(run, fake) == 0
    item = run.items[0]
    assert item.jobs[0].entry_id == 1
    assert "--no-queue" in item.jobs[0].argv
    assert "--no-push" in item.jobs[0].argv


def test_second_call_does_not_double_submit_live_job():
    run = _rebase_run(row(1, REBASE_NEED))
    fake = FakeSubmit()
    queue = {}

    def states(ids):
        return {i: queue[i] for i in ids if i in queue}

    assert _submit(run, fake, states=states) == 0
    queue[1] = EntryState.QUEUED
    n = len(fake.calls)
    assert _submit(run, fake, states=states) == 0
    assert len(fake.calls) == n


def test_busy_submit_keeps_prior_jobs():
    run = _rebase_run(row(1, REBASE_NEED), row(2, REBASE_NEED))
    fake = FakeSubmit()
    fake.busy_after = 2
    assert _submit(run, fake) == 1
    by_key = {it.key: it for it in run.items}
    assert by_key["o/r#1"].jobs[0].entry_id == 1
    assert by_key["o/r#2"].jobs == []


def test_stacked_child_not_submitted_until_parent_job_done():
    run = _rebase_run(row(1, REBASE_NEED), row(2, REBASE_NEED, base_ref="b1"))
    fake = FakeSubmit()
    queue = {}

    def states(ids):
        return {i: queue[i] for i in ids if i in queue}

    parent = run.item("o/r#1")
    child = run.item("o/r#2")
    assert child.stacked_on == parent.key
    assert _submit(run, fake, states=states) == 1
    assert len(parent.jobs) == 1
    assert child.jobs == []
    queue[parent.jobs[0].entry_id] = EntryState.DONE
    assert _submit(run, fake, states=states) == 0
    assert len(child.jobs) == 1
    assert child.jobs[0].entry_id == 2


COMMENTS_NEED = {Step.COMMENTS: NEED}


def test_default_with_ai_does_not_submit_comments():
    run = new_run([row(1, COMMENTS_NEED)], steps=[Step.COMMENTS], selected=None, pool=1,
                  with_ai=[])
    fake = FakeSubmit()
    assert _submit(run, fake, wanted=lambda item, step: True) == 0
    assert fake.calls == []
    assert run.items[0].jobs == []


def test_with_ai_comments_submits_comments_as_heavy():
    run = new_run([row(1, COMMENTS_NEED)], steps=[Step.COMMENTS], selected=None, pool=1,
                  with_ai=[Step.COMMENTS])
    fake = FakeSubmit()
    assert _submit(run, fake, wanted=lambda item, step: True) == 0
    assert len(fake.calls) == 1
    assert fake.calls[0].entry_class is EntryClass.HEAVY
    assert "comments" in fake.calls[0].argv
    assert run.items[0].jobs[0].step is Step.COMMENTS


def test_failed_ensure_worktree_is_named_and_exits_1(capsys):
    run = _rebase_run(row(1, REBASE_NEED))
    fake = FakeSubmit()
    assert _submit(run, fake, ensure_worktree=lambda item: "collision") == 1
    assert fake.calls == []
    err = capsys.readouterr().err
    assert "collision" in err
    assert run.items[0].key in err
