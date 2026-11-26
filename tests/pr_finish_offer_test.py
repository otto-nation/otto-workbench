"""pr.finish_offer and the finish-guard Stop hook: block a turn that offers a PR instead of opening it."""

import io
import json
import subprocess

import cli.finish_guard
import gh.client
import git.topology
import pr.finish_offer
import pytest

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
OFFER = "Both suites pass. Say if you want me to open the draft PR."


def _repo(path, branch):
    path.mkdir(parents=True)
    subprocess.run([*GIT, "init", "-q", "-b", branch, str(path)], check=True)
    subprocess.run([*GIT, "-C", str(path), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    return str(path)


@pytest.fixture
def asked(monkeypatch):
    """Records each `gh` lookup and answers with whatever `answer` holds."""
    state = {"calls": [], "answer": []}

    def json_out(*args, cwd=None, default=None):
        state["calls"].append(args)
        return state["answer"]

    monkeypatch.setattr(gh.client, "json_out", json_out)
    monkeypatch.setattr(git.topology, "default_branch", lambda cwd=None: "main")
    return state


@pytest.mark.parametrize("message", [
    "Say if you want me to open the draft PR.",
    "Want me to open the PR now?",
    "Should I create a pull request?",
    "If you'd like, I can open the PR.",
    "Let me know if you want me to file the PR.",
    "say if you want the PR",
])
def test_an_offer_to_open_a_pr_is_detected(message):
    assert pr.finish_offer.offers_pr(message)


@pytest.mark.parametrize("message", [
    "Opened the PR: https://github.com/o/r/pull/1",
    "Both PRs are still in their pre-push gate.",
    "Want me to rename the helper?",
    "I will open the PR once the suite is green.",
])
def test_a_report_or_an_unrelated_question_is_not_an_offer(message):
    assert not pr.finish_offer.offers_pr(message)


def test_last_assistant_text_reads_the_final_assistant_message(tmp_path):
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}},
        {"type": "user", "message": {"content": "reply"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use"}, {"type": "text", "text": "last"}]}},
    ]
    path = tmp_path / "t.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in lines) + "not json\n")
    assert pr.finish_offer.last_assistant_text(path) == "last"
    assert pr.finish_offer.last_assistant_text(tmp_path / "missing.jsonl") == ""


def test_an_offer_on_a_feature_branch_with_no_pr_blocks(tmp_path, asked):
    cwd = _repo(tmp_path / "r", "feat")
    assert pr.finish_offer.should_block(OFFER, cwd)
    assert asked["calls"][0][:3] == ("pr", "list", "--head") and asked["calls"][0][3] == "feat"


def test_an_open_pr_means_the_offer_is_moot(tmp_path, asked):
    asked["answer"] = [{"number": 7}]
    assert not pr.finish_offer.should_block(OFFER, _repo(tmp_path / "r", "feat"))


def test_a_lookup_that_failed_never_blocks(tmp_path, asked):
    asked["answer"] = None
    assert not pr.finish_offer.should_block(OFFER, _repo(tmp_path / "r", "feat"))


def test_the_default_branch_never_blocks(tmp_path, asked):
    assert not pr.finish_offer.should_block(OFFER, _repo(tmp_path / "r", "main"))
    assert asked["calls"] == []


def test_a_turn_with_no_offer_asks_gh_nothing(tmp_path, asked):
    assert not pr.finish_offer.should_block("Opened it.", _repo(tmp_path / "r", "feat"))
    assert asked["calls"] == []


def _run(monkeypatch, capsys, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    status = cli.finish_guard.main([])
    return status, capsys.readouterr().out


def test_the_hook_replies_block_with_the_reason(tmp_path, asked, monkeypatch, capsys):
    cwd = _repo(tmp_path / "r", "feat")
    status, out = _run(monkeypatch, capsys, {"cwd": cwd, "last_assistant_message": OFFER})
    assert status == 0
    assert json.loads(out) == {"decision": "block", "reason": cli.finish_guard.REASON}


def test_the_hook_reads_the_transcript_when_the_payload_has_no_message(tmp_path, asked, monkeypatch, capsys):
    cwd = _repo(tmp_path / "r", "feat")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": OFFER}]}}) + "\n")
    _, out = _run(monkeypatch, capsys, {"cwd": cwd, "transcript_path": str(transcript)})
    assert json.loads(out)["decision"] == "block"


def test_a_second_stop_is_never_blocked(tmp_path, asked, monkeypatch, capsys):
    cwd = _repo(tmp_path / "r", "feat")
    status, out = _run(monkeypatch, capsys, {"cwd": cwd, "last_assistant_message": OFFER, "stop_hook_active": True})
    assert (status, out) == (0, "")
    assert asked["calls"] == []


def test_an_unreadable_payload_exits_0_silently(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert cli.finish_guard.main([]) == 0
    assert capsys.readouterr().out == ""
