"""pi_sessions.facts: worktrees and PRs a session touched, from its own entries."""

import json
import subprocess

import pi_sessions.facts

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
ORIGIN = "https://github.com/acme/widget.git"


def _repo(path, branch="main"):
    path.mkdir(parents=True)
    subprocess.run([*GIT, "init", "-q", "-b", branch, str(path)], check=True)
    subprocess.run([*GIT, "-C", str(path), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", ORIGIN], check=True)
    return path


def _call(cid, command):
    return {"type": "message", "message": {"role": "assistant", "content": [
        {"type": "toolCall", "id": cid, "name": "bash", "arguments": {"command": command}}]}}


def _result(cid, text):
    return {"type": "message", "message": {
        "role": "toolResult", "toolCallId": cid, "toolName": "bash",
        "content": [{"type": "text", "text": text}], "isError": False}}


def _edit(cid, path):
    return {"type": "message", "message": {"role": "assistant", "content": [
        {"type": "toolCall", "id": cid, "name": "edit",
         "arguments": {"path": path, "oldText": "a", "newText": "b"}}]}}


def test_tool_calls_pairs_each_bash_call_with_its_output():
    calls = pi_sessions.facts.tool_calls(
        [_call("c1", "pr create --draft"), _result("c1", "https://github.com/acme/widget/pull/7")], "/w/a")
    assert [(c.tool_name, c.command, c.stdout, str(c.cwd)) for c in calls] == [
        ("Bash", "pr create --draft", "https://github.com/acme/widget/pull/7", "/w/a")]


def test_tool_calls_maps_edit_paths_to_absolute_file_paths():
    calls = pi_sessions.facts.tool_calls([_edit("c1", "../b/f.txt")], "/w/a")
    assert [(c.tool_name, c.file_path) for c in calls] == [("Edit", "/w/b/f.txt")]


def test_worktrees_lists_the_session_checkout_then_others_it_worked_in(tmp_path):
    home = _repo(tmp_path / "a")
    other = _repo(tmp_path / "b")
    calls = pi_sessions.facts.tool_calls([_call("c1", f"git -C {other} status")], str(home))
    found = pi_sessions.facts.worktrees_of(calls, str(home))
    assert [p.resolve() for p in found] == [home.resolve(), other.resolve()]


def test_worktrees_skips_a_cwd_outside_any_checkout(tmp_path):
    (tmp_path / "plain").mkdir()
    assert pi_sessions.facts.worktrees_of([], str(tmp_path / "plain")) == []


def test_prs_in_command_reads_gh_pr_numbers_and_the_repo_flag():
    refs = pi_sessions.facts.prs_in_command("gh pr view 12 --repo acme/other", "", "acme/widget", "github.com")
    assert refs == [pi_sessions.facts.PrRef(repo="acme/other", number=12, url="https://github.com/acme/other/pull/12")]


def test_prs_in_command_reads_pr_subcommand_numbers_and_the_pr_flag():
    refs = pi_sessions.facts.prs_in_command("pr review 5 && pr comments --pr 6", "", "acme/widget", "github.com")
    assert [r.number for r in refs] == [5, 6]
    assert {r.repo for r in refs} == {"acme/widget"}


def test_prs_in_command_reads_urls_from_a_pr_create_output():
    refs = pi_sessions.facts.prs_in_command(
        "pr create --draft", "Created https://github.com/acme/widget/pull/41\n", "acme/widget", "github.com")
    assert refs == [pi_sessions.facts.PrRef(repo="acme/widget", number=41, url="https://github.com/acme/widget/pull/41")]


def test_prs_in_command_ignores_other_commands_and_list_limits():
    assert pi_sessions.facts.prs_in_command("echo 12; gh pr list --limit 50", "", "acme/widget", "github.com") == []


def test_prs_in_command_without_a_repo_keeps_only_urls():
    assert pi_sessions.facts.prs_in_command("gh pr view 3", "", None, "github.com") == []


def test_merge_prs_dedupes_by_repo_and_number_and_keeps_the_board_title():
    board = [pi_sessions.facts.PrRef(repo="acme/widget", number=7, url="https://github.com/acme/widget/pull/7", title="feat: x")]
    seen = [pi_sessions.facts.PrRef(repo="Acme/Widget", number=7, url="u"), pi_sessions.facts.PrRef(repo="acme/widget", number=9, url="u9")]
    assert [(p.number, p.title) for p in pi_sessions.facts.merge_prs(seen, board)] == [(7, "feat: x"), (9, "")]


def test_pr_from_board_row_reads_number_repo_url_title():
    row = {"number": 7, "repo": "acme/widget", "url": "https://github.com/acme/widget/pull/7", "title": "feat: x"}
    assert pi_sessions.facts.pr_from_board_row(row) == pi_sessions.facts.PrRef(
        repo="acme/widget", number=7, url=row["url"], title="feat: x")
    assert pi_sessions.facts.pr_from_board_row({"repo": "acme/widget"}) is None


def test_collect_reads_a_session_file_end_to_end(tmp_path):
    home = _repo(tmp_path / "a")
    session = tmp_path / "s" / "x_s1.jsonl"
    session.parent.mkdir()
    header = {"type": "session", "version": 3, "id": "s1", "timestamp": "t", "cwd": str(home)}
    lines = [header, _call("c1", "gh pr view 3"), _result("c1", "")]
    session.write_text("".join(json.dumps(x) + "\n" for x in lines))
    found = pi_sessions.facts.collect(session)
    assert [w.branch for w in found.worktrees] == ["main"]
    assert found.worktrees[0].repo_key
    assert [(p.repo, p.number) for p in found.seen_prs] == [("acme/widget", 3)]
