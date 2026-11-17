"""pi_sessions.live: live-session markers, process facts and closing."""

import json
import os
import signal
import subprocess
import time

import pi_sessions.live
from pi_sessions.session_file import Header

H = Header(id="s1", cwd="/w/a", timestamp="t")


class _Ps:
    """A stand-in for subprocess.run that records argv and answers with OUT / CODE."""

    def __init__(self, out="", code=0):
        self.out, self.code, self.calls = out, code, []

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.code, stdout=self.out, stderr="")


def _session(tmp_path):
    f = tmp_path / "sessions" / "x_s1.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('{"type":"session","id":"s1","cwd":"/w/a","timestamp":"t"}\n')
    return f


def _marker(f, pid=7):
    return pi_sessions.live.Marker(pid=pid, session_id="s1", session_file=str(f), cwd="/w/a", terminal="", started_at="t")


def test_record_writes_a_marker_under_the_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    f = _session(tmp_path)
    ps = _Ps()
    marker = pi_sessions.live.record(4242, f, H, {}, run=ps)
    path = tmp_path / "state" / "pi-sessions" / "live" / "4242.json"
    assert json.loads(path.read_text())["session_id"] == "s1"
    assert marker.session_file == str(f)
    assert ps.calls == []


def test_record_labels_the_herdr_terminal_with_the_session_id(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    ps = _Ps()
    pi_sessions.live.record(1, _session(tmp_path), H, {"HC_TERMINAL_ID": "T9"}, run=ps)
    assert ps.calls == [["hc", "label", "T9", "otto.session=s1"]]


def test_clear_removes_the_marker_and_markers_lists_the_rest(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    f = _session(tmp_path)
    pi_sessions.live.record(1, f, H, {}, run=_Ps())
    pi_sessions.live.record(2, f, H, {}, run=_Ps())
    pi_sessions.live.clear(1)
    assert [m.pid for m in pi_sessions.live.markers()] == [2]
    assert pi_sessions.live.marker_for(str(f)).pid == 2
    assert pi_sessions.live.marker_for("/elsewhere.jsonl") is None


def test_state_reads_rss_and_idle_from_the_session_file_mtime(tmp_path):
    f = _session(tmp_path)
    os.utime(f, (1000, 1000))
    got = pi_sessions.live.state(_marker(f), now=1000 + 17 * 3600, run=_Ps(out="  307200\npi\n"))
    assert (got.rss_mb, got.idle_s, got.close_candidate) == (300, 17 * 3600, True)


def test_state_is_none_when_the_pid_is_gone_or_is_not_pi(tmp_path):
    f = _session(tmp_path)
    assert pi_sessions.live.state(_marker(f), now=time.time(), run=_Ps(code=1)) is None
    assert pi_sessions.live.state(_marker(f), now=time.time(), run=_Ps(out="1000\npython3\n")) is None


def test_close_candidate_needs_both_thresholds():
    assert not pi_sessions.live.Live(pid=1, rss_mb=500, idle_s=pi_sessions.live.CLOSE_IDLE_S, terminal="").close_candidate
    assert not pi_sessions.live.Live(pid=1, rss_mb=pi_sessions.live.CLOSE_RSS_MB, idle_s=pi_sessions.live.CLOSE_IDLE_S + 1, terminal="").close_candidate
    assert pi_sessions.live.Live(pid=1, rss_mb=pi_sessions.live.CLOSE_RSS_MB + 1, idle_s=pi_sessions.live.CLOSE_IDLE_S + 1, terminal="").close_candidate


def test_live_json_carries_the_close_candidate_verdict():
    doc = pi_sessions.live.Live(pid=9, rss_mb=300, idle_s=61200, terminal="T1").to_json()
    assert doc == {"pid": 9, "rss_mb": 300, "idle_s": 61200, "terminal": "T1", "close_candidate": True}


def test_running_maps_live_markers_by_session_file(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    f = _session(tmp_path)
    pi_sessions.live.record(7, f, H, {}, run=_Ps())
    got = pi_sessions.live.running(now=time.time(), run=_Ps(out="2048\npi\n"))
    assert list(got) == [str(f)]
    assert got[str(f)].pid == 7


def test_close_sends_sigterm_only_to_a_live_pi(tmp_path):
    f = _session(tmp_path)
    sent = []
    assert pi_sessions.live.close(_marker(f), run=_Ps(out="2048\npi\n"), kill=lambda pid, sig: sent.append((pid, sig)))
    assert sent == [(7, signal.SIGTERM)]
    assert not pi_sessions.live.close(_marker(f), run=_Ps(code=1), kill=lambda pid, sig: sent.append((pid, sig)))
    assert len(sent) == 1


def test_marker_for_prefers_the_marker_whose_pid_is_a_running_pi(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    f = _session(tmp_path)
    pi_sessions.live.record(1, f, H, {}, run=_Ps())
    pi_sessions.live.record(2, f, H, {}, run=_Ps())

    def ps(argv, **_kw):
        alive = argv[-1] == "2"
        return subprocess.CompletedProcess(argv, 0 if alive else 1, stdout="2048 pi\n" if alive else "", stderr="")

    assert pi_sessions.live.marker_for(str(f), run=ps).pid == 2
