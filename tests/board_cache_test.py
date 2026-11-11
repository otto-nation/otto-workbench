"""Per-repo board cache under WORKBENCH_CACHE_DIR."""

import json

from board.cache import merged, path_for, read, write
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
