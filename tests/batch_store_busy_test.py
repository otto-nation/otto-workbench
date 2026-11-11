"""`batch.store.busy_prs` over unfinished covering runs."""

import batch.model
import batch.store
from batch_session_support import seed, seed_raw


RID = "20261001T120000-abcd"


def _ref(label, key=""):
    return batch.store.RepoRef(key=key, label=label)


def _item(pr=1, repo="o/r", repo_key="", status=None):
    item = batch.model.Item(
        key=f"{repo}#{pr}",
        repo=repo,
        repo_dir="/r",
        pr=pr,
        branch="b",
        head_sha="aaa",
        steps=[
            batch.model.StepRecord(batch.model.Step.REBASE),
            batch.model.StepRecord(batch.model.Step.REVIEW),
        ],
        repo_key=repo_key,
    )
    if status is not None:
        item.status = status
    return item


def _run(run_id=RID, *, repo="o/r", status=None, repo_key="", items=None):
    run = batch.model.Run(
        id=run_id,
        started_at="t",
        steps=[batch.model.Step.REBASE, batch.model.Step.REVIEW],
        pool=2,
        auto_publish=[batch.model.Step.REBASE],
        items=items or [_item(repo=repo, repo_key=repo_key)],
        decisions=[],
    )
    if status is not None:
        run.status = status
    return run


def test_finished_run_is_not_busy():
    run = _run(status=batch.model.RunStatus.DONE)
    seed_raw(run)
    assert batch.store.busy_prs(_ref("o/r")) == frozenset()


def test_terminal_item_in_a_running_run_is_not_busy():
    items = [
        _item(pr=1, status=batch.model.ItemStatus.DONE),
        _item(pr=2, status=batch.model.ItemStatus.QUEUED),
    ]
    seed(_run(items=items))
    assert batch.store.busy_prs(_ref("o/r")) == frozenset({2})


def test_covering_matches_by_repo_key_even_when_the_label_differs():
    seed(_run(repo="other/label", repo_key="acme-widget-abcd1234"))
    assert batch.store.busy_prs(_ref("cwd/label", key="acme-widget-abcd1234")) == frozenset({1})
    assert batch.store.busy_prs(_ref("other/label", key="zzz")) == frozenset()


def test_covering_falls_back_to_the_label_when_an_item_has_no_repo_key():
    seed(_run(repo="o/r"))
    assert batch.store.busy_prs(_ref("O/R", key="unrelated-key-00000000")) == frozenset({1})


def test_busy_prs_none_ref_is_empty():
    seed(_run())
    assert batch.store.busy_prs(None) == frozenset()
