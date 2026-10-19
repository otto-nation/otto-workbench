import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.live  # noqa: E402
import batch.plan  # noqa: E402
from batch.model import STEP_ORDER, Item, ItemStatus, Run, RunStatus, Step  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from conftest import git_in, init_repo  # noqa: E402

CLEAR = {s: StepNeed(False, "fine") for s in STEP_ORDER}


def _item(n, repo="o/r", repo_dir="/r", sha="s1", status=ItemStatus.DONE, **kw):
    return Item(key=f"{repo}#{n}", repo=repo, repo_dir=repo_dir, pr=n, branch=f"b{n}",
                head_sha=sha, status=status, **kw)


def _run(items):
    return Run(id="r1", started_at="t", steps=list(STEP_ORDER), pool=1, auto_publish=[],
               status=RunStatus.DONE, items=list(items))


def _row(n, sha="s1", needs=None, repo="o/r"):
    return PlanRow(repo, "/r", n, f"t{n}", f"b{n}", sha, False, needs or dict(CLEAR))


def _checkout(tmp_path, name, slug):
    repo = init_repo(tmp_path / name)
    git_in(repo, "remote", "add", "origin", f"https://github.com/{slug}.git")
    return repo


def test_compare_reports_untracked_moved_closed_and_owed_prs():
    run = _run([_item(1), _item(2), _item(3)])
    plan = Plan("me", [_row(1), _row(2, sha="s2"),
                       _row(4, needs={**CLEAR, Step.REBASE: StepNeed(True, "3 behind main")})])
    got = batch.live.compare(run, plan, {"o/r"})
    assert got == {
        "open": 3,
        "untracked": [{"pr": "o/r#4", "title": "t4", "branch": "b4", "head_sha": "s1"}],
        "moved": [{"pr": "o/r#2", "run_sha": "s1", "head_sha": "s2"}],
        "closed": ["o/r#3"],
        "owed": [{"pr": "o/r#4", "needs": {"rebase": "3 behind main"}}],
    }


def test_a_head_the_run_published_is_not_reported_as_moved():
    run = _run([_item(1, published_sha="s2")])
    assert batch.live.compare(run, Plan("me", [_row(1, sha="s2")]), {"o/r"})["moved"] == []


def test_items_of_an_unsearched_repo_are_not_reported_closed():
    run = _run([_item(1), _item(2, repo="o/other")])
    assert batch.live.compare(run, Plan("me", [_row(1)]), {"o/r"})["closed"] == []


def test_an_item_the_run_already_skipped_as_closed_is_not_reported_again():
    run = _run([_item(1, status=ItemStatus.SKIPPED_CLOSED)])
    assert batch.live.compare(run, Plan("me", []), {"o/r"})["closed"] == []


def test_checkouts_take_the_items_repo_dir_when_its_origin_matches(tmp_path):
    repo = _checkout(tmp_path, "repo", "o/r")
    run = _run([_item(1, repo_dir=str(repo))])
    assert batch.live.checkouts(run, cwd=str(tmp_path)) == {"o/r": str(repo)}


def test_a_relative_repo_dir_naming_another_repo_falls_back_to_the_cwd(tmp_path):
    _checkout(tmp_path, "elsewhere", "o/elsewhere")
    repo = _checkout(tmp_path, "repo", "o/r")
    # "." from an older run, read from a directory that is a different repo.
    run = _run([_item(1, repo_dir="../elsewhere")])
    assert batch.live.checkouts(run, cwd=str(repo)) == {"o/r": str(repo)}


def test_no_matching_checkout_reports_unchecked_without_asking_github(tmp_path):
    _checkout(tmp_path, "repo", "o/elsewhere")
    run = _run([_item(1, repo_dir=".")])

    def no_github(dirs):
        raise AssertionError("searched GitHub with no checkout")

    got = batch.live.view(run, cwd=str(tmp_path / "repo"), plan_fn=no_github)
    assert got["checked"] is False and got["unchecked"] == ["o/r"]
    assert "no checkout" in got["error"]


def test_a_github_failure_is_reported_in_the_section(tmp_path):
    repo = _checkout(tmp_path, "repo", "o/r")

    def failing(dirs):
        raise batch.plan.PlanError("GitHub query failed: rate limited")

    got = batch.live.view(_run([_item(1, repo_dir=str(repo))]), cwd=str(tmp_path),
                          plan_fn=failing)
    assert got == {"checked": False, "repos": ["o/r"], "unchecked": [],
                   "error": "GitHub query failed: rate limited"}


def test_view_drops_the_plans_refs(tmp_path, monkeypatch):
    repo = _checkout(tmp_path, "repo", "o/r")
    dropped = []
    monkeypatch.setattr(batch.plan, "drop_refs", lambda dirs, ns: dropped.append((dirs, ns)))
    plan = Plan("me", [_row(1)], ref_namespace="refs/pr-batch/x", ref_dirs=[str(repo)])
    got = batch.live.view(_run([_item(1, repo_dir=str(repo))]), cwd=str(tmp_path),
                          plan_fn=lambda dirs: plan)
    assert got["checked"] is True and got["viewer"] == "me"
    assert dropped == [([str(repo)], "refs/pr-batch/x")]


def test_owes_only_for_a_checked_section_with_work():
    quiet = {"checked": True, "owed": [], "untracked": [], "moved": []}
    assert batch.live.owes(quiet) is False
    assert batch.live.owes({**quiet, "untracked": [{"pr": "o/r#4"}]}) is True
    assert batch.live.owes({"checked": False, "error": "x"}) is False
    assert batch.live.owes(None) is False
