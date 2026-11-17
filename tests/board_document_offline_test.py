"""Board document offline reads: cache-only phases, no gh calls, and cache age."""

from board.cache import write
from board.document import build
from board.model import Phase
from board_document_support import (
    board_env,
    pull_facts,
)


def _offline_gh_raises(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("gh call")

    monkeypatch.setattr("board.github.list_pulls", boom)
    monkeypatch.setattr("gh.client.login", boom)
    monkeypatch.setattr("gh.client.graphql", boom)


def test_offline_makes_no_gh_calls(tmp_path, monkeypatch):
    found = board_env(tmp_path, monkeypatch)
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
    found = board_env(tmp_path, monkeypatch)
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


def test_offline_cached_at_is_listing_fetch_time(tmp_path, monkeypatch):
    found = board_env(tmp_path, monkeypatch, pulls=[pull_facts(7, "aaa")])
    t1, t2 = "2026-01-01T00:00:00Z", "2026-06-15T12:00:00Z"
    monkeypatch.setattr("board.document._now", lambda: t1)
    build(Phase.LISTING, [found.path])
    monkeypatch.setattr("board.document._now", lambda: t2)
    _offline_gh_raises(monkeypatch)
    doc = build(Phase.LISTING, [found.path], offline=True)
    assert doc["repos"][0]["cached_at"] == t1
    assert doc["repos"][0]["fetched_at"] == t2


def test_offline_cached_at_prefers_status_over_listing(tmp_path, monkeypatch):
    found = board_env(tmp_path, monkeypatch)
    t0, t1 = "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"
    write(found.key, Phase.LISTING, {
        "fetched_at": t0,
        "rows": [{
            "number": 7,
            "head_sha": "a",
            "title": "T",
            "author": "me",
            "repo": "acme/box",
        }],
    })
    write(found.key, Phase.STATUS, {
        "fetched_at": t1,
        "rows": [{
            "number": 7,
            "head_sha": "a",
            "ci_status": "passing",
            "ci_unknown": False,
        }],
    })
    _offline_gh_raises(monkeypatch)
    doc = build(Phase.LISTING, [found.path], offline=True)
    assert doc["repos"][0]["cached_at"] == t1


def test_offline_queued_has_no_cached_at(tmp_path, monkeypatch):
    found = board_env(tmp_path, monkeypatch)
    _offline_gh_raises(monkeypatch)
    doc = build(Phase.LISTING, [found.path], offline=True)
    assert doc["repos"][0]["phase"] == "queued"
    assert doc["repos"][0]["cached_at"] is None
