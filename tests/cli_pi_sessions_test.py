"""`pi-sessions`: list, show, facts, live and close wiring."""

import json
import os
import subprocess

import cli.pi_sessions
import pi_sessions.claude
import pi_sessions.live
import pr.target
from board.cache import write
from board.model import Phase
from pi_sessions.session_file import BRIEF_TYPE

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
BRIEF = {"type": "custom", "customType": BRIEF_TYPE, "data": {"title_set": "T", "name": "T", "tldr": "x"}}


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("HC_TERMINAL_ID", raising=False)


def _session(tmp_path, sid="s1", cwd="/w/a", entries=(BRIEF,)):
    path = tmp_path / "sessions" / "--w-a--" / f"2026-11-17T00-00-00-000Z_{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {"type": "session", "version": 3, "id": sid, "timestamp": "t", "cwd": cwd}
    path.write_text("".join(json.dumps(e) + "\n" for e in [header, *entries]))
    return path


def _repo(path):
    path.mkdir(parents=True)
    subprocess.run([*GIT, "init", "-q", "-b", "feat", str(path)], check=True)
    subprocess.run([*GIT, "-C", str(path), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin",
                    "https://github.com/acme/widget.git"], check=True)
    return path


def test_list_json_prints_schema_window_and_sessions(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    _session(tmp_path)
    assert cli.pi_sessions.main(["list", "--json", "--since", "3"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert (doc["schema"], doc["since_days"]) == (1, 3)
    assert [s["id"] for s in doc["sessions"]] == ["s1"]


def test_list_defaults_to_nine_days_and_all_adds_unbriefed(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    _session(tmp_path, sid="bare", entries=())
    assert cli.pi_sessions.main(["list", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert (doc["since_days"], doc["sessions"]) == (9, [])
    assert cli.pi_sessions.main(["list", "--json", "--all"]) == 0
    assert [s["id"] for s in json.loads(capsys.readouterr().out)["sessions"]] == ["bare"]


def test_show_unknown_id_exits_1(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    assert cli.pi_sessions.main(["show", "nope", "--json"]) == 1
    assert "no pi session nope" in capsys.readouterr().err


def test_facts_merges_command_prs_with_the_board_cache(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    repo = _repo(tmp_path / "w")
    write(pr.target.repo_key_from_origin(str(repo)), Phase.LISTING, {"rows": [
        {"number": 7, "branch": "feat", "state": "OPEN", "repo": "acme/widget",
         "url": "https://github.com/acme/widget/pull/7", "title": "feat: x"}]})
    call = {"type": "message", "message": {"role": "assistant", "content": [
        {"type": "toolCall", "id": "c1", "name": "bash", "arguments": {"command": "gh pr view 9"}}]}}
    path = _session(tmp_path, cwd=str(repo), entries=[call])
    assert cli.pi_sessions.main(["facts", str(path), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert [(p["number"], p["title"]) for p in doc["prs"]] == [(7, "feat: x"), (9, "")]
    assert [os.path.realpath(w) for w in doc["worktrees"]] == [os.path.realpath(repo)]


def test_live_records_and_clear_removes_the_marker(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    path = _session(tmp_path)
    assert cli.pi_sessions.main(["live", "--pid", "77", "--session-file", str(path)]) == 0
    assert [m.pid for m in pi_sessions.live.markers()] == [77]
    assert cli.pi_sessions.main(["live", "--clear", "--pid", "77"]) == 0
    assert pi_sessions.live.markers() == []


def test_live_without_a_session_file_or_clear_is_a_usage_error(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    assert cli.pi_sessions.main(["live", "--pid", "1"]) == 2
    assert "live needs --session-file or --clear" in capsys.readouterr().err


def test_close_a_session_that_is_not_live_exits_1(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    _session(tmp_path)
    assert cli.pi_sessions.main(["close", "s1"]) == 1
    assert "s1 is not running here" in capsys.readouterr().err


def test_close_signals_the_live_pi(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    path = _session(tmp_path)
    assert cli.pi_sessions.main(["live", "--pid", "77", "--session-file", str(path)]) == 0
    sent = []
    monkeypatch.setattr(pi_sessions.live, "_rss_kb_if_pi", lambda pid, run: 1024)
    monkeypatch.setattr(pi_sessions.live.os, "kill", lambda pid, sig: sent.append(pid))
    assert cli.pi_sessions.main(["close", "s1"]) == 0
    assert sent == [77]


def _claude(tmp_path, monkeypatch, sid="c1", entries=({"type": "ai-title", "aiTitle": "Claude work"},)):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    path = tmp_path / "claude" / "projects" / "-w-a" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "user", "cwd": "/w/a", "timestamp": "t"}, *entries]
    path.write_text("".join(json.dumps(e) + "\n" for e in lines))
    return path


def test_list_json_carries_pi_and_claude_rows_with_their_harness(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    _session(tmp_path)
    _claude(tmp_path, monkeypatch)
    assert cli.pi_sessions.main(["list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)["sessions"]
    assert sorted((r["id"], r["harness"]) for r in rows) == [("c1", "claude"), ("s1", "pi")]


def test_show_finds_a_claude_session(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    _claude(tmp_path, monkeypatch)
    assert cli.pi_sessions.main(["show", "c1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["harness"] == "claude"


def test_close_signals_a_live_claude(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    _claude(tmp_path, monkeypatch)
    record = tmp_path / "claude" / "sessions" / "88.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"pid": 88, "sessionId": "c1", "cwd": "/w/a"}))
    sent = []
    monkeypatch.setattr(pi_sessions.claude, "rss_kb_if_command", lambda pid, command, run: 1024)
    monkeypatch.setattr(pi_sessions.claude.os, "kill", lambda pid, sig: sent.append(pid))
    assert cli.pi_sessions.main(["close", "c1"]) == 0
    assert sent == [88]
