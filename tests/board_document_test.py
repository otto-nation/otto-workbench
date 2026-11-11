"""Board document: listing, status, owed, stamp, and per-repo errors."""

from board.cache import path_for, read, write
from board.document import Checkout, build, stamp
from board.github import StatusFetch
from board.model import CiStatus, Mergeable, Phase, PullFacts, StatusFacts
from batch.model import Step
from batch.plan import Plan, PlanRow, StepNeed


def _co(tmp_path, key="k"):
    return Checkout(str(tmp_path / "co"), "acme/box", "github.com", key)


def _env(tmp_path, monkeypatch, *, checkout=None, pulls=None, status=None, owed_plan=None):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))
    found = checkout or _co(tmp_path)
    monkeypatch.setattr("board.document.gh.client.login", lambda: "me")
    monkeypatch.setattr("board.document.review.listing.rows", lambda: [])
    monkeypatch.setattr("board.document.batch.store.busy_prs", lambda ref: frozenset())
    monkeypatch.setattr("board.document._resolve", lambda path: found)
    monkeypatch.setattr(
        "board.document.github.list_pulls",
        lambda slug: pulls if pulls is not None else [],
    )

    def fake_fetch(slug, numbers, *, executor=None):
        if status is not None:
            return status
        return StatusFetch(by_number={}, partial=False)

    monkeypatch.setattr("board.document.github.fetch_status", fake_fetch)
    if owed_plan is not None:
        monkeypatch.setattr(
            "board.document.batch.plan.build_plan",
            lambda checkouts, steps=None: owed_plan,
        )
    return found


def _pull(number, head_sha, *, title="T", author="me"):
    return PullFacts(
        repo="acme/box",
        number=number,
        title=title,
        author=author,
        head_sha=head_sha,
    )


def test_listing_unhydrated_unless_cached_status_head_sha_matches(tmp_path, monkeypatch):
    found = _env(tmp_path, monkeypatch, pulls=[
        _pull(7, "aaa"),
        _pull(8, "bbb", title="U", author="you"),
    ])
    write(found.key, Phase.STATUS, {
        "rows": [
            {
                "number": 7,
                "head_sha": "aaa",
                "ci_status": "passing",
                "ci_unknown": False,
                "mergeable": "MERGEABLE",
            },
            {
                "number": 8,
                "head_sha": "stale",
                "ci_status": "failing",
                "ci_unknown": False,
                "mergeable": "CONFLICTING",
            },
        ],
    })
    doc = build(Phase.LISTING, [found.path])
    by_number = {row["number"]: row for row in doc["rows"]}
    assert doc["complete"] is True
    assert doc["repos"][0]["phase"] == "hydrating"
    assert by_number[7]["hydrated"] is True
    assert by_number[7]["ci_status"] == "passing"
    assert by_number[7]["ci_unknown"] is False
    assert by_number[8]["hydrated"] is False
    assert by_number[8]["ci_unknown"] is True
    assert by_number[8]["mergeable"] == "UNKNOWN"
    assert "ci_status" in by_number[8]
    assert by_number[8]["ci_status"] == "none"


def test_status_writes_cache_and_sets_partial(tmp_path, monkeypatch):
    facts = StatusFacts(
        ci_status=CiStatus.PASSING,
        ci_unknown=False,
        mergeable=Mergeable.MERGEABLE,
    )
    found = _env(
        tmp_path,
        monkeypatch,
        pulls=[_pull(7, "aaa"), _pull(8, "bbb", title="U", author="you")],
        status=StatusFetch(by_number={7: facts}, partial=True),
    )
    doc = build(Phase.STATUS, [found.path])
    assert doc["repos"][0]["partial"] is True
    assert doc["repos"][0]["phase"] == "loaded"
    assert doc["complete"] is True
    cached = read(found.key, Phase.STATUS)
    assert cached is not None
    numbers = {row["number"] for row in cached["rows"]}
    assert 7 in numbers
    assert 8 not in numbers
    by_number = {row["number"]: row for row in doc["rows"]}
    assert by_number[7]["hydrated"] is True
    assert by_number[7]["ci_status"] == "passing"
    assert by_number[8]["hydrated"] is False
    assert path_for(found.key, Phase.STATUS).is_file()


def test_status_prs_is_a_patch(tmp_path, monkeypatch):
    facts = StatusFacts(
        ci_status=CiStatus.PASSING,
        ci_unknown=False,
        mergeable=Mergeable.MERGEABLE,
    )
    found = _env(
        tmp_path,
        monkeypatch,
        pulls=[_pull(7, "aaa"), _pull(8, "bbb", title="U", author="you")],
        status=StatusFetch(by_number={7: facts}, partial=False),
    )
    doc = build(Phase.STATUS, [found.path], prs=[7])
    assert doc["complete"] is False
    assert [row["number"] for row in doc["rows"]] == [7]
    assert doc["rows"][0]["hydrated"] is True


def test_owed_flips_mine_lane_to_has_comments(tmp_path, monkeypatch):
    found = _co(tmp_path)
    plan = Plan(
        viewer="me",
        rows=[
            PlanRow(
                repo="acme/box",
                repo_dir=found.path,
                pr=7,
                title="T",
                branch="b",
                head_sha="aaa",
                is_draft=False,
                needs={Step.COMMENTS: StepNeed(True, "2 unresolved threads")},
            ),
        ],
    )
    _env(tmp_path, monkeypatch, checkout=found, owed_plan=plan)
    write(found.key, Phase.LISTING, {
        "rows": [{
            "number": 7,
            "head_sha": "aaa",
            "title": "T",
            "author": "me",
            "repo": "acme/box",
        }],
    })
    doc = build(Phase.OWED, [found.path])
    assert doc["complete"] is False
    assert len(doc["rows"]) == 1
    row = doc["rows"][0]
    assert row["owed"] == "2 unresolved threads"
    assert row["owed_known"] is True
    assert row["is_mine"] is True
    assert row["lanes"]["mine"] == "has-comments"


def test_stamp_stable_across_fetched_at(tmp_path, monkeypatch):
    found = _env(tmp_path, monkeypatch, pulls=[_pull(7, "aaa")])
    times = iter(["2026-01-01T00:00:00Z", "2026-06-15T12:00:00Z"])
    monkeypatch.setattr("board.document._now", lambda: next(times))
    first = build(Phase.LISTING, [found.path])
    second = build(Phase.LISTING, [found.path])
    assert first["stamp"] == second["stamp"]
    assert first["repos"][0]["fetched_at"] != second["repos"][0]["fetched_at"]
    assert stamp(first) == first["stamp"]


def test_if_changed_unchanged_envelope(tmp_path, monkeypatch):
    found = _env(tmp_path, monkeypatch, pulls=[_pull(7, "aaa")])
    monkeypatch.setattr("board.document._now", lambda: "2026-01-01T00:00:00Z")
    doc = build(Phase.LISTING, [found.path])
    again = build(Phase.LISTING, [found.path], if_changed=doc["stamp"])
    assert again == {
        "schema_version": 1,
        "phase": "listing",
        "stamp": doc["stamp"],
        "unchanged": True,
    }


def test_missing_checkout_is_repo_error_and_still_a_document(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("board.document.gh.client.login", lambda: "me")
    monkeypatch.setattr("board.document.review.listing.rows", lambda: [])
    missing = str(tmp_path / "no-such-checkout")
    doc = build(Phase.LISTING, [missing])
    assert doc["schema_version"] == 1
    assert doc["stamp"]
    assert doc["complete"] is True
    assert doc["rows"] == []
    repo = doc["repos"][0]
    assert repo["checkout"] == missing
    assert repo["phase"] == "failed"
    assert repo["error"]["kind"] == "no-checkout"
