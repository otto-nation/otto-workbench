"""pi_sessions.listing: which sessions to list and what each row says."""

import json
import os

import pi_sessions.listing
from pi_sessions.live import Live
from pi_sessions.session_file import BRIEF_TYPE

DAY = 86400
NOW = 1_800_000_000
BRIEF = {"type": "custom", "customType": BRIEF_TYPE, "data": {
    "title_set": "Fix login", "name": "Fix login", "tldr": "Fixing the login race.",
    "worktrees": ["/w/a"], "prs": [{"repo": "acme/widget", "number": 7, "url": "u", "title": "t"}],
    "at": "x"}}


def _session(root, sid, *, age_days, entries=(BRIEF,)):
    path = root / "--w-a--" / f"2026-11-01T00-00-00-000Z_{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {"type": "session", "version": 3, "id": sid, "timestamp": "2026-11-01T00:00:00.000Z", "cwd": "/w/a"}
    path.write_text("".join(json.dumps(e) + "\n" for e in [header, *entries]))
    mtime = NOW - age_days * DAY
    os.utime(path, (mtime, mtime))
    return path


def _ids(rows):
    return [r.id for r in rows]


def test_recent_briefed_sessions_are_listed_newest_first(tmp_path):
    _session(tmp_path, "old", age_days=3)
    _session(tmp_path, "new", age_days=1)
    assert _ids(pi_sessions.listing.list_sessions(tmp_path, now=NOW, since_days=9, live={})) == ["new", "old"]


def test_a_recent_session_with_no_brief_name_or_process_is_left_out_unless_all(tmp_path):
    _session(tmp_path, "bare", age_days=1, entries=())
    assert pi_sessions.listing.list_sessions(tmp_path, now=NOW, since_days=9, live={}) == []
    assert _ids(pi_sessions.listing.list_sessions(tmp_path, now=NOW, since_days=9, live={}, include_all=True)) == ["bare"]


def test_an_unnamed_session_older_than_the_window_is_left_out(tmp_path):
    _session(tmp_path, "stale", age_days=10, entries=())
    _session(tmp_path, "stale-briefed", age_days=10)
    assert pi_sessions.listing.list_sessions(tmp_path, now=NOW, since_days=9, live={}, include_all=True) == []


def test_a_named_session_is_listed_regardless_of_age(tmp_path):
    _session(tmp_path, "named", age_days=40, entries=[{"type": "session_info", "name": "Keep me"}])
    rows = pi_sessions.listing.list_sessions(tmp_path, now=NOW, since_days=9, live={})
    assert [(r.id, r.name) for r in rows] == [("named", "Keep me")]


def test_a_live_session_is_listed_regardless_of_age(tmp_path):
    path = _session(tmp_path, "running", age_days=30, entries=())
    alive = Live(pid=9, rss_mb=300, idle_s=30 * DAY, terminal="")
    rows = pi_sessions.listing.list_sessions(tmp_path, now=NOW, since_days=9, live={str(path): alive})
    assert [(r.id, r.live) for r in rows] == [("running", alive)]


def test_since_widens_the_window(tmp_path):
    _session(tmp_path, "stale", age_days=10)
    assert _ids(pi_sessions.listing.list_sessions(tmp_path, now=NOW, since_days=18, live={})) == ["stale"]


def test_row_json_carries_the_brief_and_live_facts(tmp_path):
    path = _session(tmp_path, "s1", age_days=0)
    alive = Live(pid=9, rss_mb=300, idle_s=17 * 3600, terminal="T1")
    doc = pi_sessions.listing.list_sessions(tmp_path, now=NOW, since_days=9, live={str(path): alive})[0].to_json()
    assert (doc["id"], doc["file"], doc["cwd"]) == ("s1", str(path), "/w/a")
    assert (doc["name"], doc["title_set"], doc["tldr"]) == ("Fix login", "Fix login", "Fixing the login race.")
    assert doc["worktrees"] == ["/w/a"]
    assert doc["prs"][0]["number"] == 7
    assert doc["live"] == {"pid": 9, "rss_mb": 300, "idle_s": 61200, "terminal": "T1", "close_candidate": True}
    assert doc["last_active"] == "2027-01-15T08:00:00Z"


def test_show_finds_one_session_by_id_whatever_its_age(tmp_path):
    _session(tmp_path, "far", age_days=100, entries=())
    assert pi_sessions.listing.show(tmp_path, "far", live={}).id == "far"
    assert pi_sessions.listing.show(tmp_path, "missing", live={}) is None
