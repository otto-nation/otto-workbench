"""Comments-owed map from a comments-only batch plan."""

from types import SimpleNamespace

from board.owed import (
    OWED_TTL_MS,
    Owed,
    load_owed,
    owed_from,
    reason_for,
    owed_due,
)
from batch.model import Step
from batch.plan import Plan, PlanRow, StepNeed


def test_owed_from_keeps_only_this_repos_rows_whose_comments_need_is_set():
    doc = {
        "rows": [
            {
                "repo": "O/N",
                "pr": 7,
                "head_sha": "aaa",
                "needs": {"comments": {"needed": True, "reason": "2 unresolved threads"}},
            },
            {
                "repo": "o/n",
                "pr": 8,
                "needs": {"comments": {"needed": False, "reason": "no unresolved threads"}},
            },
            {
                "repo": "o/other",
                "pr": 9,
                "needs": {"comments": {"needed": True, "reason": "x"}},
            },
            {
                "repo": "o/n",
                "pr": "10",
                "needs": {"comments": {"needed": True, "reason": "x"}},
            },
            {"repo": "o/n", "pr": 11, "needs": {}},
        ],
    }
    assert owed_from(doc, "o/n") == {7: Owed(reason="2 unresolved threads", head_sha="aaa")}
    assert owed_from(None, "o/n") == {}


def test_load_owed_runs_the_comments_only_plan_and_maps_it(monkeypatch):
    seen = []

    def fake_build(checkouts, steps=None):
        seen.append((list(checkouts), steps))
        return Plan(
            viewer="me",
            rows=[
                PlanRow(
                    repo="o/n",
                    repo_dir="/co",
                    pr=3,
                    title="t",
                    branch="b",
                    head_sha="abc",
                    is_draft=False,
                    needs={Step.COMMENTS: StepNeed(True, "1 PR comment")},
                ),
            ],
        )

    monkeypatch.setattr("board.owed.batch.plan.build_plan", fake_build)
    got = load_owed(["/co"], "o/n")
    assert seen == [(["/co"], frozenset({Step.COMMENTS}))]
    assert got == {3: Owed(reason="1 PR comment", head_sha="abc")}


def test_reason_for_puts_each_prs_owed_feedback_on_it_null_when_none_is_known():
    owed = {7: Owed(reason="2 unresolved threads", head_sha="a")}
    assert [reason_for(7, owed), reason_for(8, owed)] == ["2 unresolved threads", None]
    assert reason_for(7, None) is None


def test_owed_due_is_true_when_owed_at_is_missing_or_the_ttl_has_elapsed():
    now = 1e12
    ttl = OWED_TTL_MS
    assert [
        owed_due(None, now),
        owed_due(float("nan"), now),
        owed_due(now - ttl, now),
        owed_due(now - ttl - 1, now),
        owed_due(now - ttl + 1, now),
        owed_due(now - 10, now, 10),
        owed_due(now - 9, now, 10),
    ] == [True, True, True, True, False, True, False]


def test_owed_from_accepts_a_plan_row():
    plan = SimpleNamespace(rows=[
        PlanRow(
            repo="o/n",
            repo_dir="/co",
            pr=4,
            title="t",
            branch="b",
            head_sha="def",
            is_draft=False,
            needs={Step.COMMENTS: StepNeed(True, "1 PR comment")},
        ),
    ])
    assert owed_from(plan, "o/n") == {4: Owed(reason="1 PR comment", head_sha="def")}
