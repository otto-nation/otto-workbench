"""Standup gatherer — GitHub calls are faked."""

from datetime import datetime, timezone

import gh.client
from standup.gather import gather, main, resolve_org


def test_resolve_org_prefers_flag():
    assert resolve_org("/tmp", "acme") == "acme"


def test_resolve_org_from_cwd_repo(monkeypatch):
    monkeypatch.setattr(
        gh.client, "json_out",
        lambda *a, **kw: {"owner": {"login": "acme"}},
    )
    assert resolve_org("/tmp/repo", None) == "acme"


def test_gather_buckets_open_and_recent_merges(monkeypatch):
    def fake_json_out(*args, **_kw):
        if args[:2] == ("search", "prs") and "--merged" in args:
            return [
                {
                    "number": 10,
                    "title": "old",
                    "url": "https://github.com/acme/r/pull/10",
                    "additions": 1,
                    "closedAt": "2026-01-01T00:00:00Z",
                    "repository": {"nameWithOwner": "acme/r"},
                },
                {
                    "number": 11,
                    "title": "fresh",
                    "url": "https://github.com/acme/r/pull/11",
                    "additions": 3,
                    "closedAt": "2026-11-20T12:00:00Z",
                    "repository": {"nameWithOwner": "acme/r"},
                },
            ]
        if args[:2] == ("search", "prs"):
            return [{"number": 1, "repository": {"nameWithOwner": "acme/r"}}]
        if args[:2] == ("pr", "list"):
            return [{
                "number": 1,
                "title": "open one",
                "url": "https://github.com/acme/r/pull/1",
                "isDraft": False,
                "mergeable": "MERGEABLE",
                "reviewDecision": "REVIEW_REQUIRED",
                "additions": 8,
                "deletions": 1,
                "statusCheckRollup": [{"name": "Unit", "conclusion": "SUCCESS"}],
            }]
        return []

    monkeypatch.setattr(gh.client, "json_out", fake_json_out)
    payload = gather(
        "acme", "ada", 7,
        now=datetime(2026, 11, 23, tzinfo=timezone.utc),
    )
    assert [r["number"] for r in payload["buckets"]["waiting_on_review"]] == [1]
    assert [r["number"] for r in payload["buckets"]["landed"]] == [11]


def test_main_fails_when_org_missing(monkeypatch, capsys):
    monkeypatch.setattr("standup.gather.resolve_org", lambda *_a, **_k: None)
    assert main(["--cwd", "/tmp"]) == 2
    assert "pass --org" in capsys.readouterr().err


def test_main_uses_at_me_when_login_blank(monkeypatch):
    monkeypatch.setattr("standup.gather.resolve_org", lambda *_a, **_k: "acme")
    monkeypatch.setattr(gh.client, "login", lambda: "")
    captured: dict = {}

    def fake_gather(org, author, days):
        captured["author"] = author
        return {"org": org, "author": author, "days": days, "buckets": {}}

    monkeypatch.setattr("standup.gather.gather", fake_gather)
    monkeypatch.setattr("standup.gather.json.dump", lambda *_a, **_k: None)
    assert main([]) == 0
    assert captured["author"] == "@me"
