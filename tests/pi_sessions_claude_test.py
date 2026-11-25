"""pi_sessions.claude and the Claude rows of pi_sessions.listing: transcripts, titles, live records."""

import json
import os
import signal
from types import SimpleNamespace

import pi_sessions.claude
import pi_sessions.listing
from pi_sessions.live import Live

DAY = 86400
NOW = 1_800_000_000
CWD = "/w/a"


def _transcript(root, sid, *, age_days=0, entries=()):
    path = root / "projects" / "-w-a" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lead = [{"type": "mode", "sessionId": sid}, {"type": "user", "cwd": CWD, "timestamp": "2026-11-01T00:00:00.000Z"}]
    path.write_text("".join(json.dumps(e) + "\n" for e in [*lead, *entries]))
    mtime = NOW - age_days * DAY
    os.utime(path, (mtime, mtime))
    return path


def _record(root, pid, sid):
    path = root / "sessions" / f"{pid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "sessionId": sid, "cwd": CWD}))


def _ps(command, rss_kb=2048):
    def run(argv, **_):
        return SimpleNamespace(returncode=0, stdout=f"{rss_kb} /usr/bin/{command}\n")
    return run


AI = {"type": "ai-title", "aiTitle": "Fix the login race"}
CUSTOM = {"type": "custom-title", "customTitle": "Login work"}


def test_header_takes_the_id_from_the_file_and_the_cwd_from_the_first_entry_naming_one(tmp_path):
    header = pi_sessions.claude.read_header(_transcript(tmp_path, "abc"))
    assert (header.id, header.cwd, header.timestamp) == ("abc", CWD, "2026-11-01T00:00:00.000Z")


def test_a_transcript_naming_no_cwd_has_no_header(tmp_path):
    path = tmp_path / "projects" / "-w-a" / "x.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"type": "mode"}) + "\n")
    assert pi_sessions.claude.read_header(path) is None


def test_the_latest_title_of_each_kind_is_read(tmp_path):
    older = {"type": "ai-title", "aiTitle": "Old"}
    path = _transcript(tmp_path, "abc", entries=[older, AI, CUSTOM])
    assert pi_sessions.claude.read_titles(path) == pi_sessions.claude.Titles(custom="Login work", ai="Fix the login race")


def test_a_transcript_with_no_title_has_none(tmp_path):
    assert pi_sessions.claude.read_titles(_transcript(tmp_path, "abc")) == pi_sessions.claude.Titles(custom=None, ai=None)


def test_a_recent_titled_transcript_is_listed_as_a_claude_row(tmp_path):
    path = _transcript(tmp_path, "abc", age_days=1, entries=[AI])
    row = pi_sessions.listing.list_claude(tmp_path, now=NOW, since_days=9, live={})[0]
    doc = row.to_json()
    assert (doc["harness"], doc["id"], doc["file"], doc["cwd"]) == ("claude", "abc", str(path), CWD)
    assert (doc["name"], doc["title_set"], doc["tldr"], doc["worktrees"], doc["prs"]) == (
        None, "Fix the login race", "", [], [])


def test_pi_rows_say_pi(tmp_path):
    assert pi_sessions.listing.SessionRow.__dataclass_fields__["harness"].default == "pi"


def test_an_untitled_transcript_is_left_out_unless_all(tmp_path):
    _transcript(tmp_path, "bare", age_days=1)
    assert pi_sessions.listing.list_claude(tmp_path, now=NOW, since_days=9, live={}) == []
    rows = pi_sessions.listing.list_claude(tmp_path, now=NOW, since_days=9, live={}, include_all=True)
    assert [r.id for r in rows] == ["bare"]


def test_an_old_titled_transcript_is_left_out_but_a_named_or_live_one_stays(tmp_path):
    _transcript(tmp_path, "stale", age_days=30, entries=[AI])
    named = _transcript(tmp_path, "named", age_days=30, entries=[CUSTOM])
    live_path = _transcript(tmp_path, "running", age_days=30)
    alive = Live(pid=9, rss_mb=300, idle_s=30 * DAY, terminal="")
    rows = pi_sessions.listing.list_claude(tmp_path, now=NOW, since_days=9, live={str(live_path): alive})
    assert sorted(r.id for r in rows) == ["named", "running"]
    assert next(r for r in rows if r.id == "named").file == str(named)


def test_running_reads_the_live_record_and_checks_the_process_is_claude(tmp_path):
    path = _transcript(tmp_path, "abc", age_days=1)
    _record(tmp_path, 42, "abc")
    got = pi_sessions.claude.running(tmp_path, NOW, run=_ps("claude", 3072))
    assert got == {str(path): Live(pid=42, rss_mb=3, idle_s=DAY, terminal="")}
    assert pi_sessions.claude.running(tmp_path, NOW, run=_ps("node")) == {}


def test_a_live_record_with_no_transcript_is_ignored(tmp_path):
    _record(tmp_path, 42, "ghost")
    assert pi_sessions.claude.running(tmp_path, NOW, run=_ps("claude")) == {}


def test_close_signals_only_the_claude_process_of_that_session(tmp_path):
    _record(tmp_path, 42, "abc")
    _record(tmp_path, 43, "other")
    sent = []
    kill = lambda pid, sig: sent.append((pid, sig))  # noqa: E731
    assert pi_sessions.claude.close(tmp_path, "abc", run=_ps("claude"), kill=kill)
    assert sent == [(42, signal.SIGTERM)]
    assert not pi_sessions.claude.close(tmp_path, "abc", run=_ps("node"), kill=kill)
    assert not pi_sessions.claude.close(tmp_path, "missing", run=_ps("claude"), kill=kill)
