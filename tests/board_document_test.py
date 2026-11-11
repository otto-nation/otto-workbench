"""Board document: listing, status, owed, stamp, and per-repo errors."""

import json
from datetime import datetime, timezone
from pathlib import Path

import board.reviews
import core.serde
from board.cache import path_for, read, write
from board.document import Checkout, build, stamp
from board.github import PullList, StatusFetch
from board.model import CiStatus, Mergeable, Phase, PrState, PullFacts, StatusFacts
from batch.model import Step
from batch.plan import Plan, PlanRow, StepNeed
from conftest import init_repo

DOCUMENT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "board_document.json"


def _co(tmp_path, key="k"):
    return Checkout(str(tmp_path / "co"), "acme/box", "github.com", key)


def _env(tmp_path, monkeypatch, *, checkout=None, pulls=None, status=None, owed_plan=None, listing=None):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))
    found = checkout or _co(tmp_path)
    listing_rows = list(listing or [])
    monkeypatch.setattr("gh.client.login", lambda: "me")
    monkeypatch.setattr("board.document.review.listing.rows", lambda: listing_rows)
    monkeypatch.setattr("board.rows.batch.store.busy_prs", lambda ref: frozenset())
    monkeypatch.setattr("board.document._resolve", lambda path: found)
    monkeypatch.setattr(
        "board.github.list_pulls",
        lambda slug: PullList(pulls=list(pulls) if pulls is not None else []),
    )

    def fake_fetch(slug, numbers, *, executor=None):
        if status is not None:
            return status
        return StatusFetch(by_number={}, partial=False)

    monkeypatch.setattr("board.github.fetch_status", fake_fetch)
    if owed_plan is not None:
        monkeypatch.setattr(
            "board.document.batch.plan.build_plan",
            lambda checkouts, steps=None: owed_plan,
        )
    return found


def _pull(number, head_sha, *, title="T", author="me", **kwargs):
    return PullFacts(
        repo="acme/box",
        number=number,
        title=title,
        author=author,
        head_sha=head_sha,
        **kwargs,
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
    assert doc["reviews_root"]
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
    assert row["presentation"]["ref"] == "box#7"
    assert list(row["presentation"]["row_badges"]) == [
        {"kind": "status", "status": "none", "text": "CI loading"},
        {"kind": "status", "status": "warn", "text": "2 unresolved threads"},
    ]


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
    monkeypatch.setattr("gh.client.login", lambda: "me")
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


def test_failed_listing_fetch_keeps_cache_and_reports_error(tmp_path, monkeypatch):
    found = _env(tmp_path, monkeypatch)
    write(found.key, Phase.LISTING, {"rows": [{"number": 7, "head_sha": "a"}]})
    monkeypatch.setattr(
        "board.github.list_pulls",
        lambda slug: PullList(pulls=None, error="API rate limit exceeded"),
    )
    for phase in (Phase.LISTING, Phase.STATUS):
        doc = build(phase, [found.path])
        assert doc["repos"][0]["phase"] == "failed"
        assert doc["repos"][0]["error"]["kind"] == "gh-failed"
        assert doc["repos"][0]["error"]["message"] == "API rate limit exceeded"
        assert read(found.key, Phase.LISTING)["rows"][0]["number"] == 7


def test_listing_auth_failure_is_auth_kind(tmp_path, monkeypatch):
    found = _env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "board.github.list_pulls",
        lambda slug: PullList(pulls=None, error="HTTP 401: Bad credentials"),
    )
    doc = build(Phase.LISTING, [found.path])
    assert doc["repos"][0]["error"]["kind"] == "auth"
    assert doc["repos"][0]["error"]["message"] == "HTTP 401: Bad credentials"


def test_not_git_is_distinct_from_no_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("gh.client.login", lambda: "me")
    monkeypatch.setattr("board.document.review.listing.rows", lambda: [])
    plain = tmp_path / "plain"
    plain.mkdir()
    not_git = build(Phase.LISTING, [str(plain)])
    assert not_git["repos"][0]["error"]["kind"] == "not-git"
    repo = init_repo(tmp_path / "repo")
    no_origin = build(Phase.LISTING, [str(repo)])
    assert no_origin["repos"][0]["error"]["kind"] == "no-origin"


def test_status_reuses_listing_cache_within_60s(tmp_path, monkeypatch):
    pull = _pull(7, "aaa")
    found = _env(tmp_path, monkeypatch, pulls=[pull])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write(found.key, Phase.LISTING, {
        "fetched_at": now,
        "rows": [core.serde.to_dict(pull)],
    })

    def boom(slug):
        raise AssertionError("list_pulls")

    monkeypatch.setattr("board.github.list_pulls", boom)
    doc = build(Phase.STATUS, [found.path])
    assert doc["repos"][0]["phase"] == "loaded"


def test_status_refetches_listing_when_cache_is_stale(tmp_path, monkeypatch):
    pull = _pull(7, "aaa")
    found = _env(tmp_path, monkeypatch, pulls=[pull])
    write(found.key, Phase.LISTING, {
        "fetched_at": "2020-01-01T00:00:00Z",
        "rows": [core.serde.to_dict(pull)],
    })
    calls = {"n": 0}

    def fake(slug):
        calls["n"] += 1
        return PullList(pulls=[pull])

    monkeypatch.setattr("board.github.list_pulls", fake)
    build(Phase.STATUS, [found.path])
    assert calls["n"] == 1


def test_status_prs_does_not_freshen_partial_listing(tmp_path, monkeypatch):
    seven, eight = _pull(7, "aaa"), _pull(8, "bbb")
    found = _env(tmp_path, monkeypatch, pulls=[seven, eight])
    write(found.key, Phase.LISTING, {
        "fetched_at": "2020-01-01T00:00:00Z",
        "rows": [core.serde.to_dict(seven)],
    })
    build(Phase.STATUS, [found.path], prs=[7])

    def boom(slug):
        raise AssertionError("list_pulls")

    monkeypatch.setattr("board.github.list_pulls", boom)
    doc = build(Phase.STATUS, [found.path])
    assert {row["number"] for row in doc["rows"]} == {7, 8}


def _offline_gh_raises(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("gh call")

    monkeypatch.setattr("board.github.list_pulls", boom)
    monkeypatch.setattr("gh.client.login", boom)
    monkeypatch.setattr("gh.client.graphql", boom)


def test_offline_makes_no_gh_calls(tmp_path, monkeypatch):
    found = _env(tmp_path, monkeypatch)
    write(found.key, Phase.LISTING, {
        "rows": [{
            "number": 7,
            "head_sha": "a",
            "title": "T",
            "author": "me",
            "repo": "acme/box",
        }],
    })
    _offline_gh_raises(monkeypatch)
    doc = build(Phase.LISTING, [found.path], offline=True)
    assert doc["repos"][0]["phase"] == "hydrating"
    assert [row["number"] for row in doc["rows"]] == [7]


def test_offline_repo_phases_from_cache(tmp_path, monkeypatch):
    found = _env(tmp_path, monkeypatch)
    _offline_gh_raises(monkeypatch)
    queued = build(Phase.LISTING, [found.path], offline=True)
    assert queued["repos"][0]["phase"] == "queued"
    assert queued["rows"] == []

    write(found.key, Phase.LISTING, {
        "rows": [{
            "number": 7,
            "head_sha": "a",
            "title": "T",
            "author": "me",
            "repo": "acme/box",
        }],
    })
    hydrating = build(Phase.LISTING, [found.path], offline=True)
    assert hydrating["repos"][0]["phase"] == "hydrating"
    assert [row["number"] for row in hydrating["rows"]] == [7]

    write(found.key, Phase.STATUS, {
        "rows": [{
            "number": 7,
            "head_sha": "a",
            "ci_status": "passing",
            "ci_unknown": False,
        }],
    })
    loaded = build(Phase.LISTING, [found.path], offline=True)
    assert loaded["repos"][0]["phase"] == "loaded"
    assert loaded["rows"][0]["hydrated"] is True


_FIXTURE_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
_FIXTURE_NOW_ISO = "2026-01-15T12:00:00Z"


def _as_json(value):
    return json.loads(json.dumps(value))


def _scrub_document(value):
    """Drop invocation-specific document fields; keep review-run stamps."""
    doc = _as_json(value)
    if not isinstance(doc, dict):
        return doc
    doc.pop("stamp", None)
    doc.pop("reviews_root", None)
    for repo in doc.get("repos") or []:
        if isinstance(repo, dict):
            repo.pop("fetched_at", None)
            repo.pop("checkout", None)
    return doc


def _fixture_pulls():
    return [
        _pull(
            7, "aaa",
            title="Fix comments",
            author="me",
            branch="me/fix/comments",
            base_branch="main",
            url="https://github.com/acme/box/pull/7",
            labels=("comments",),
        ),
        _pull(
            8, "bbb",
            title="Unhydrated work",
            author="you",
            branch="you/feat/wip",
            base_branch="main",
            url="https://github.com/acme/box/pull/8",
        ),
        _pull(
            10, "ddd",
            title="WIP draft",
            author="pat",
            branch="pat/feat/draft",
            base_branch="main",
            url="https://github.com/acme/box/pull/10",
            is_draft=True,
        ),
    ]


def _fixture_listing():
    return [
        {
            "repo": "acme/box",
            "mode": "pr",
            "pr_number": 7,
            "host": "github.com",
            "head_sha": "aaa",
            "stamp": "20260102T000000",
            "structured": True,
            "cost_usd": 1.25,
            "input_tokens": 10,
            "output_tokens": 20,
            "review_file": "/r.md",
            "status": "completed",
            "reviewed_at": "2026-01-02T00:00:00Z",
            "verdict": "ok",
            "findings": {"must_fix": 0},
            "post": {"event": "comment", "posted_at": "2026-01-02T12:00:00Z"},
            "history": [{
                "head_sha": "bbb",
                "stamp": "20260101T000000",
                "structured": False,
                "post": {"event": "comment", "posted_at": "2026-01-01T08:00:00Z"},
            }],
        },
        {
            "repo": "acme/box",
            "mode": "pr",
            "pr_number": 9,
            "host": "github.com",
            "head_sha": "ccc",
            "head_ref": "you/feat/landed",
            "reviewed_at": "2026-01-10T00:00:00Z",
            "status": "completed",
            "post": None,
            "history": [],
        },
    ]


def _fixture_status():
    return StatusFetch(
        by_number={
            7: StatusFacts(
                additions=12,
                deletions=3,
                changed_files=2,
                mergeable=Mergeable.MERGEABLE,
                ci_status=CiStatus.PASSING,
                ci_unknown=False,
            ),
            9: StatusFacts(
                additions=20,
                deletions=5,
                changed_files=4,
                mergeable=Mergeable.UNKNOWN,
                ci_status=CiStatus.PASSING,
                ci_unknown=False,
                review_decision="APPROVED",
                merged_at="2026-01-10T00:00:00Z",
                state=PrState.MERGED,
            ),
            10: StatusFacts(
                additions=4,
                deletions=1,
                changed_files=1,
                mergeable=Mergeable.MERGEABLE,
                ci_status=CiStatus.NONE,
                ci_unknown=False,
            ),
        },
        partial=False,
    )


def _build_status_document(tmp_path, monkeypatch):
    found = _env(
        tmp_path,
        monkeypatch,
        pulls=_fixture_pulls(),
        status=_fixture_status(),
        listing=_fixture_listing(),
    )
    write(found.key, Phase.OWED, {
        "rows": [{
            "number": 7,
            "reason": "2 unresolved threads",
            "head_sha": "aaa",
        }],
    })
    monkeypatch.setattr("board.document._now", lambda: _FIXTURE_NOW_ISO)
    real_keep = board.reviews.keep_landed
    monkeypatch.setattr(
        "board.reviews.keep_landed",
        lambda entries, now=None: real_keep(entries, now=_FIXTURE_NOW),
    )
    return build(Phase.STATUS, [found.path])


def test_status_document_fixture_matches_after_scrub(tmp_path, monkeypatch):
    doc = _build_status_document(tmp_path, monkeypatch)
    expected = json.loads(DOCUMENT_FIXTURE.read_text(encoding="utf-8"))
    assert [row["number"] for row in doc["rows"]] == [7, 8, 10, 9]
    assert doc["rows"][0]["is_mine"] is True
    assert doc["rows"][0]["owed"] == "2 unresolved threads"
    assert len(doc["rows"][0]["review_runs"]) == 2
    assert doc["rows"][0]["review_runs"][0]["post"]["event"] == "comment"
    assert doc["rows"][1]["hydrated"] is False
    assert doc["rows"][1]["author"] == "you"
    assert doc["rows"][2]["is_draft"] is True
    assert doc["rows"][3]["state"] == "MERGED"
    assert _scrub_document(doc) == _scrub_document(expected)
