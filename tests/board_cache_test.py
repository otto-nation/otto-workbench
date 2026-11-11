"""Per-repo board cache under WORKBENCH_CACHE_DIR."""

import json

import os
import time

from board.cache import (
    VIEWER_TTL_S,
    merged,
    path_for,
    read,
    read_viewer,
    viewer,
    viewer_path,
    write,
    write_viewer,
)
from board.model import Phase


def test_write_and_read_round_trip_under_workbench_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))
    payload = {
        "fetched_at": "2026-06-15T00:00:00Z",
        "rows": [{"number": 7, "head_sha": "a", "title": "T"}],
    }
    write("acme-widget-abcd", Phase.LISTING, payload)
    path = path_for("acme-widget-abcd", Phase.LISTING)
    assert path == tmp_path / "board" / "acme-widget-abcd" / "listing.json"
    assert json.loads(path.read_text()) == payload
    assert read("acme-widget-abcd", Phase.LISTING) == payload


def test_read_missing_or_unreadable_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))
    assert read("missing", Phase.STATUS) is None
    write("k", Phase.STATUS, {"rows": []})
    path_for("k", Phase.STATUS).write_text("{not json")
    assert read("k", Phase.STATUS) is None


def test_merged_applies_status_only_when_head_sha_matches(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))
    write("k", Phase.LISTING, {
        "rows": [
            {"number": 7, "head_sha": "a", "title": "T"},
            {"number": 8, "head_sha": "b", "title": "U"},
        ],
    })
    write("k", Phase.STATUS, {
        "rows": [
            {"number": 7, "head_sha": "a", "ci_status": "passing", "ci_unknown": False},
            {"number": 8, "head_sha": "stale", "ci_status": "failing", "ci_unknown": False},
        ],
    })
    rows = merged("k")
    assert rows[7]["hydrated"] is True
    assert rows[7]["ci_status"] == "passing"
    assert rows[7]["title"] == "T"
    assert rows[8]["hydrated"] is False
    assert "ci_status" not in rows[8]


def test_merged_does_not_resurrect_open_status_only_row(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))
    write("k", Phase.LISTING, {
        "rows": [{"number": 1, "head_sha": "a", "title": "open"}],
    })
    write("k", Phase.STATUS, {
        "rows": [
            {"number": 1, "head_sha": "a", "ci_status": "passing"},
            {
                "number": 9,
                "head_sha": "z",
                "state": "OPEN",
                "title": "closed-on-github",
            },
            {
                "number": 10,
                "head_sha": "m",
                "state": "MERGED",
                "title": "landed",
            },
            {
                "number": 11,
                "head_sha": "c",
                "state": "CLOSED",
                "title": "closed",
            },
        ],
    })
    rows = merged("k")
    assert 9 not in rows
    assert rows[10]["hydrated"] is True
    assert rows[10]["title"] == "landed"
    assert rows[11]["hydrated"] is True
    assert 1 in rows


def test_merged_overlays_owed_by_number(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))
    write("k", Phase.LISTING, {"rows": [{"number": 7, "head_sha": "a"}]})
    write("k", Phase.OWED, {
        "rows": [{"number": 7, "reason": "2 unresolved threads", "head_sha": "a"}],
    })
    row = merged("k")[7]
    # The OWED phase row's `reason` key surfaces as `owed` on the merged row.
    assert row["owed"] == "2 unresolved threads"
    assert row["owed_known"] is True
    assert row["owed_head_sha"] == "a"


def test_viewer_writes_under_board_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("gh.client.login", lambda: "me")
    assert viewer(refresh=True) == "me"
    assert viewer_path() == tmp_path / "board" / "viewer.json"
    assert read_viewer() == "me"


def test_viewer_fresh_skips_login(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))
    write_viewer("cached")

    def boom():
        raise AssertionError("login")

    monkeypatch.setattr("gh.client.login", boom)
    assert viewer(refresh=True) == "cached"


def test_viewer_refresh_when_older_than_one_hour(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))
    write_viewer("old")
    path = viewer_path()
    past = time.time() - (VIEWER_TTL_S + 1)
    os.utime(path, (past, past))
    monkeypatch.setattr("gh.client.login", lambda: "new")
    assert viewer(refresh=True) == "new"
    assert read_viewer() == "new"


def test_viewer_offline_skips_login(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))
    write_viewer("cached")

    def boom():
        raise AssertionError("login")

    monkeypatch.setattr("gh.client.login", boom)
    assert viewer(refresh=False) == "cached"
    assert viewer(refresh=False) == "cached"


def test_viewer_offline_missing_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))

    def boom():
        raise AssertionError("login")

    monkeypatch.setattr("gh.client.login", boom)
    assert viewer(refresh=False) == ""
