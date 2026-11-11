"""REST and GraphQL mapping for the PR board's GitHub reads."""

import json
import threading
import time

from board.github import (
    GRAPHQL_BATCH,
    PULLS_JQ,
    POOL_WORKERS,
    batches,
    fetch_status,
    list_pulls,
    map_graphql,
    map_pull,
    map_status,
    status_query,
)
from board.model import CiStatus, Mergeable
from core.proc import CmdResult
import pytest


def _rest(**overlay):
    body = {
        "number": 3,
        "title": "t",
        "user": {"login": "ada"},
        "head": {"ref": "feat", "sha": "abc"},
        "base": {"ref": "main"},
        "draft": False,
        "html_url": "https://ex/3",
        "labels": [{"name": "bug"}],
        "created_at": "c",
        "updated_at": "u",
    }
    body.update(overlay)
    return body


def _gql_pr(**overlay):
    body = {
        "isDraft": False,
        "reviewDecision": None,
        "mergeable": "MERGEABLE",
        "additions": 0,
        "deletions": 0,
        "changedFiles": 0,
        "mergeQueueEntry": None,
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
        "latestReviews": {"nodes": []},
        "reviewRequests": {"nodes": []},
        "state": "OPEN",
        "mergedAt": None,
    }
    body.update(overlay)
    return body


def _gql_payload(pr, *, extra=None):
    repo = {"pr0": pr}
    if extra:
        repo.update(extra)
    return {"data": {"repository": repo}}


def test_batches_boundaries():
    assert batches([]) == []
    assert batches([1]) == [[1]]
    fifty = list(range(50))
    assert len(batches(fifty)) == 1
    assert len(batches(fifty)[0]) == 50
    sizes = [len(b) for b in batches(list(range(51)))]
    assert sizes == [50, 1]
    assert GRAPHQL_BATCH == 50


def test_status_query_drops_non_integers_and_aliases_each_number():
    query = status_query([12, "x", 3.5, 7, True])
    assert "pr0: pullRequest(number: 12)" in query
    assert "pr1: pullRequest(number: 7)" in query
    assert "number: 3.5" not in query
    assert "number: x" not in query
    assert "number: True" not in query
    assert "number: 1)" not in query


def test_map_pulls_paged_flat_empty():
    rest = _rest()
    want = map_pull(rest, "acme/box")
    assert want.number == 3
    assert want.title == "t"
    assert want.author == "ada"
    assert want.branch == "feat"
    assert want.base_branch == "main"
    assert want.is_draft is False
    assert want.url == "https://ex/3"
    assert want.head_sha == "abc"
    assert want.labels == ("bug",)
    assert want.created_at == "c"
    assert want.updated_at == "u"
    assert want.repo == "acme/box"


def test_map_pull_null_labels():
    facts = map_pull(_rest(labels=None))
    assert facts.labels == ()


@pytest.mark.parametrize(
    "state,expected",
    [
        ("SUCCESS", CiStatus.PASSING),
        ("FAILURE", CiStatus.FAILING),
        ("ERROR", CiStatus.FAILING),
        ("PENDING", CiStatus.PENDING),
        ("EXPECTED", CiStatus.PENDING),
        (None, CiStatus.NONE),
        ("WEIRD", CiStatus.NONE),
    ],
)
def test_map_status_ci_status(state, expected):
    rollup = None if state is None else {"state": state}
    pr = _gql_pr(commits={"nodes": [{"commit": {"statusCheckRollup": rollup}}]})
    assert map_status(pr).ci_status == expected
    assert map_status(pr).ci_unknown is False


def test_map_status_draft_keeps_real_ci():
    pr = _gql_pr(
        isDraft=True,
        commits={"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]},
    )
    assert map_status(pr).ci_status == CiStatus.FAILING


@pytest.mark.parametrize(
    "value,expected",
    [
        ("MERGEABLE", Mergeable.MERGEABLE),
        ("CONFLICTING", Mergeable.CONFLICTING),
        ("FOO", Mergeable.UNKNOWN),
        (None, Mergeable.UNKNOWN),
    ],
)
def test_map_status_mergeable(value, expected):
    assert map_status(_gql_pr(mergeable=value)).mergeable == expected


def test_map_graphql_drops_team_reviewers_and_keeps_user():
    pr = _gql_pr(
        reviewRequests={
            "nodes": [
                {"requestedReviewer": {"__typename": "Team", "login": "devs"}},
                {"requestedReviewer": {"__typename": "User", "login": "ada"}},
            ]
        }
    )
    mapped = map_graphql(_gql_payload(pr), [1])
    assert mapped[1].requested_reviewers == ("ada",)


def test_map_graphql_skips_a_null_pr():
    assert map_graphql({"data": {"repository": {"pr0": None}}}, [9]) == {}


def test_map_graphql_merge_queue_entry_sets_in_merge_queue():
    mapped = map_graphql(_gql_payload(_gql_pr(mergeQueueEntry={"id": "MQE_1"})), [1])
    assert mapped[1].in_merge_queue is True


def test_map_graphql_maps_partial_data_when_errors_are_present():
    pr = _gql_pr(additions=1, changedFiles=1, commits={"nodes": []})
    payload = {
        "data": {"repository": {"pr0": pr, "pr1": None}},
        "errors": [{"message": "x"}],
    }
    mapped = map_graphql(payload, [10, 11])
    assert 10 in mapped
    assert 11 not in mapped
    assert mapped[10].additions == 1


@pytest.mark.parametrize("payload", [None, "nope", 1, []])
def test_map_graphql_garbage_input_is_empty(payload):
    assert map_graphql(payload, [1]) == {}


def test_list_pulls_maps_rest_pages(monkeypatch):
    rest = _rest()

    def fake_api(endpoint, **kwargs):
        assert "repos/acme/box/pulls" in endpoint
        assert "state=open" in endpoint
        assert kwargs.get("paginate") is True
        assert kwargs.get("jq") == PULLS_JQ
        return CmdResult(stdout=json.dumps([rest]) + "\n" + json.dumps([rest]))

    monkeypatch.setattr("board.github.gh.client.api", fake_api)
    pulls = list_pulls("acme/box")
    assert len(pulls) == 2
    assert pulls[0].author == "ada"
    assert pulls[0].repo == "acme/box"


def test_list_pulls_failure_is_none_not_empty(monkeypatch):
    monkeypatch.setattr(
        "board.github.gh.client.api", lambda endpoint, **kw: CmdResult(returncode=1)
    )
    assert list_pulls("acme/box") is None


def test_owner_name_are_graphql_variables(monkeypatch):
    seen = {}

    def fake_graphql(query, *, variables=None, **_kw):
        seen["query"] = query
        seen["variables"] = variables
        return CmdResult(stdout='{"data":{"repository":{}}}')

    monkeypatch.setattr("board.github.gh.client.graphql", fake_graphql)
    fetch_status("acme/box", [12])
    assert "$owner" in seen["query"]
    assert "$name" in seen["query"]
    assert "repository(owner: $owner, name: $name)" in seen["query"]
    assert 'owner: "acme"' not in seen["query"]
    assert seen["variables"] == {"owner": "acme", "name": "box"}
    assert "pr0: pullRequest(number: 12)" in seen["query"]


def test_fetch_status_caps_pool_at_4(monkeypatch):
    current = 0
    peak = 0
    lock = threading.Lock()
    calls = {"n": 0}

    def fake_graphql(query, *, variables=None, **_kw):
        nonlocal current, peak
        with lock:
            calls["n"] += 1
            current += 1
            peak = max(peak, current)
        time.sleep(0.05)
        with lock:
            current -= 1
        return CmdResult(stdout='{"data":{"repository":{}}}')

    monkeypatch.setattr("board.github.gh.client.graphql", fake_graphql)
    numbers = list(range(1, 1 + GRAPHQL_BATCH * 8))
    result = fetch_status("acme/box", numbers)
    assert result.partial is False
    assert calls["n"] == 8
    assert peak == POOL_WORKERS
    assert peak <= 4


def test_failing_batch_is_partial_and_leaves_rows_unhydrated(monkeypatch):
    def fake_graphql(query, *, variables=None, **_kw):
        if "number: 51" in query:
            return CmdResult(returncode=1, stderr="boom")
        pr = _gql_pr()
        aliases = {}
        for i in range(GRAPHQL_BATCH):
            aliases[f"pr{i}"] = pr
        return CmdResult(stdout=json.dumps({"data": {"repository": aliases}}))

    monkeypatch.setattr("board.github.gh.client.graphql", fake_graphql)
    result = fetch_status("acme/box", list(range(1, 52)))
    assert result.partial is True
    assert 1 in result.by_number
    assert 50 in result.by_number
    assert 51 not in result.by_number
