"""pi_sessions.session_file: pi's session JSONL — header, tail, entries, lookup."""

import json

import pi_sessions.session_file

HEADER = {"type": "session", "version": 3, "id": "s1",
          "timestamp": "2026-11-17T10:00:00.000Z", "cwd": "/w/a"}


def _write(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return path


def _msg(i, role="assistant"):
    return {"type": "message", "id": f"m{i}", "parentId": None, "timestamp": "t",
            "message": {"role": role, "content": [{"type": "text", "text": "x" * 10}]}}


def _brief(**data):
    return {"type": "custom", "customType": pi_sessions.session_file.BRIEF_TYPE, "data": data}


def test_sessions_root_honours_pi_session_dir_env(tmp_path):
    assert pi_sessions.session_file.sessions_root({"PI_CODING_AGENT_SESSION_DIR": str(tmp_path)}) == tmp_path


def test_sessions_root_defaults_under_home_when_env_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    got = pi_sessions.session_file.sessions_root({"PI_CODING_AGENT_SESSION_DIR": ""})
    assert got == tmp_path / ".pi" / "agent" / "sessions"


def test_read_header_returns_id_cwd_timestamp(tmp_path):
    path = _write(tmp_path / "d" / "x_s1.jsonl", [HEADER, _msg(1)])
    assert pi_sessions.session_file.read_header(path) == pi_sessions.session_file.Header(
        id="s1", cwd="/w/a", timestamp="2026-11-17T10:00:00.000Z")


def test_read_header_is_none_for_a_file_that_is_not_a_session(tmp_path):
    path = _write(tmp_path / "d" / "x.jsonl", [_msg(1)])
    assert pi_sessions.session_file.read_header(path) is None


def test_read_tail_takes_the_last_brief_and_its_recorded_name(tmp_path):
    path = _write(tmp_path / "d" / "x_s1.jsonl",
                  [HEADER, _brief(tldr="old", name="A"), _msg(1), _brief(tldr="new", name="B"), _msg(2)])
    tail = pi_sessions.session_file.read_tail(path)
    assert tail.brief == {"tldr": "new", "name": "B"}
    assert tail.name == "B"


def test_read_tail_a_rename_after_the_brief_wins(tmp_path):
    rename = {"type": "session_info", "id": "i1", "parentId": None, "timestamp": "t", "name": "Mine"}
    path = _write(tmp_path / "d" / "x_s1.jsonl", [HEADER, _brief(tldr="t", name="auto"), rename])
    assert pi_sessions.session_file.read_tail(path).name == "Mine"


def test_read_tail_a_cleared_name_after_the_brief_is_none(tmp_path):
    cleared = {"type": "session_info", "id": "i1", "parentId": None, "timestamp": "t"}
    path = _write(tmp_path / "d" / "x_s1.jsonl", [HEADER, _brief(tldr="t", name="auto"), cleared])
    assert pi_sessions.session_file.read_tail(path).name is None


def test_read_tail_without_a_brief_reports_the_latest_session_info(tmp_path):
    path = _write(tmp_path / "d" / "x_s1.jsonl", [
        HEADER, {"type": "session_info", "name": "One"}, _msg(1),
        {"type": "session_info", "name": "Two"}, _msg(2)])
    tail = pi_sessions.session_file.read_tail(path)
    assert (tail.name, tail.brief) == ("Two", None)


def test_read_tail_stops_at_the_byte_limit_and_reads_across_blocks(tmp_path):
    filler = [_msg(i) for i in range(2000)]
    path = _write(tmp_path / "d" / "x_s1.jsonl",
                  [HEADER, {"type": "session_info", "name": "Far back"}, *filler])
    assert pi_sessions.session_file.read_tail(path, limit=4096).name is None
    assert pi_sessions.session_file.read_tail(path).name == "Far back"


def test_iter_entries_skips_the_header_and_unparseable_lines(tmp_path):
    path = tmp_path / "d" / "x_s1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(HEADER) + "\n{not json\n" + json.dumps(_msg(1)) + "\n")
    assert [e["id"] for e in pi_sessions.session_file.iter_entries(path)] == ["m1"]


def test_find_session_by_id_and_by_path(tmp_path):
    path = _write(tmp_path / "--w-a--" / "2026-11-17T10-00-00-000Z_s1.jsonl", [HEADER])
    assert pi_sessions.session_file.find_session(tmp_path, "s1") == path
    assert pi_sessions.session_file.find_session(tmp_path, str(path)) == path
    assert pi_sessions.session_file.find_session(tmp_path, "nope") is None


def test_session_files_are_sorted(tmp_path):
    b = _write(tmp_path / "b" / "2_s2.jsonl", [HEADER])
    a = _write(tmp_path / "a" / "1_s1.jsonl", [HEADER])
    assert pi_sessions.session_file.session_files(tmp_path) == [a, b]
