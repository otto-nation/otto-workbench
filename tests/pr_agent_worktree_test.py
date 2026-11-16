"""Tests for pr.agent_worktree — which worktree an agent's tool call worked in."""

import json
import re
import subprocess
import sys
from pathlib import Path
from shlex import quote

import pytest

from conftest import commit_all, git_in, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.topology  # noqa: E402
import pr.agent_worktree  # noqa: E402
from pr.agent_worktree import LABEL, ReportOutcome, ToolCall, clear, report, target_worktree  # noqa: E402


@pytest.fixture
def container(tmp_path) -> Path:
    """A bare-repo container: `main`, `feat-a` on `feat/a`, and a detached `feat-b`."""
    seed = init_repo(tmp_path / "seed")
    (seed / "f").write_text("f\n")
    commit_all(seed, "base")
    root = tmp_path / "c"
    root.mkdir()
    git_in(tmp_path, "clone", "--bare", "-q", str(seed), str(root / ".git"))
    bare = root / ".git"
    git_in(bare, "worktree", "add", "-q", str(root / "main"), "main")
    git_in(bare, "worktree", "add", "-q", "-b", "feat/a", str(root / "feat-a"))
    git_in(bare, "worktree", "add", "-q", "--detach", str(root / "feat-b"))
    return root


def _spelling(container: Path, name: str) -> Path:
    """The worktree path exactly as `git worktree list` spells it."""
    for entry in git.topology.worktree_entries(str(container / "main")):
        if entry.path.name == name:
            return entry.path
    raise AssertionError(f"no worktree named {name}")


def _bash(container: Path, command: str, stdout: str = "", **extra) -> ToolCall:
    home = str(container / "main")
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"stdout": stdout},
        "cwd": home,
        **extra,
    }
    return ToolCall.from_payload(payload, {"CLAUDE_PROJECT_DIR": home})


def test_label_key():
    assert LABEL == "otto.worktree"


@pytest.mark.parametrize("template", [
    "git -C {a} status",
    "pr create --repo-dir {a}",
    "pr create --repo-dir={a}",
    "cd {a} && make",
])
def test_command_naming_feat_a_targets_it(container, template):
    command = template.format(a=quote(str(container / "feat-a")))
    assert target_worktree(_bash(container, command)) == _spelling(container, "feat-a")


def test_edit_of_a_file_not_yet_created_targets_its_worktree(container):
    (container / "feat-a" / "x").mkdir()
    home = str(container / "main")
    call = ToolCall.from_payload(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(container / "feat-a" / "x" / "y.py")},
            "cwd": home,
        },
        {"CLAUDE_PROJECT_DIR": home},
    )
    assert target_worktree(call) == _spelling(container, "feat-a")


def test_wt_switch_branch_is_looked_up_after_the_fact(container):
    call = _bash(container, "wt switch -c feat/a")
    assert target_worktree(call) == _spelling(container, "feat-a")


def test_wt_switch_pr_shortcut_reads_json_stdout_path(container):
    stdout = json.dumps({"action": "switched", "path": str(container / "feat-b")}) + "\n"
    call = _bash(container, "wt switch pr:3 --format json", stdout=stdout)
    assert target_worktree(call) == _spelling(container, "feat-b")


def test_wt_switch_pr_shortcut_without_stdout_names_nothing(container):
    assert target_worktree(_bash(container, "wt switch pr:3")) is None


def test_quoted_command_inside_echo_is_not_a_candidate(container):
    command = f'echo "x; git -C {container / "feat-a"} status"'
    assert target_worktree(_bash(container, command)) is None


def test_heredoc_body_is_not_a_candidate(container):
    command = f"cat <<EOF\ngit -C {container / 'feat-a'} status\nEOF"
    assert target_worktree(_bash(container, command)) is None


def test_home_checkout_is_never_the_target(container):
    command = f"git -C {quote(str(container / 'main'))} log"
    assert target_worktree(_bash(container, command)) is None


def test_last_candidate_wins(container):
    a = quote(str(container / "feat-a"))
    b = quote(str(container / "feat-b"))
    call = _bash(container, f"git -C {a} log; git -C {b} log")
    assert target_worktree(call) == _spelling(container, "feat-b")


def test_path_outside_any_checkout_targets_nothing(container, tmp_path):
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    assert target_worktree(_bash(container, f"git -C {quote(str(nowhere))} status")) is None


def test_agent_id_marks_a_subagent(container):
    assert _bash(container, "ls", agent_id="abc").subagent is True
    assert _bash(container, "ls").subagent is False


def test_claude_payload_cwd_away_from_home_is_the_target(container):
    home = str(container / "main")
    call = ToolCall.from_payload(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "cwd": str(container / "feat-a"),
        },
        {"CLAUDE_PROJECT_DIR": home},
    )
    assert target_worktree(call) == _spelling(container, "feat-a")


def test_payload_cwd_in_a_subdirectory_of_home_is_none(container):
    home = container / "main"
    sub = home / "sub"
    sub.mkdir()
    call = ToolCall.from_payload(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(sub)},
        {"CLAUDE_PROJECT_DIR": str(home)},
    )
    assert target_worktree(call) is None


def test_command_cd_beats_stale_payload_cwd(container):
    home = str(container / "main")
    call = ToolCall.from_payload(
        {
            "tool_name": "Bash",
            "tool_input": {"command": f"cd {quote(str(container / 'feat-b'))} && ls"},
            "cwd": str(container / "feat-a"),
        },
        {"CLAUDE_PROJECT_DIR": home},
    )
    assert target_worktree(call) == _spelling(container, "feat-b")


def test_symlinked_alias_resolves_to_worktree_list_spelling(container, tmp_path):
    """git records feat-a under a symlinked alias, while `--show-toplevel` answers the
    realpath: the answer must be the spelling `git worktree list` prints."""
    alias = tmp_path / "alias"
    alias.symlink_to(container)
    gitdir = container / ".git" / "worktrees" / "feat-a" / "gitdir"
    gitdir.write_text(f"{alias / 'feat-a' / '.git'}\n")
    listed = _spelling(container, "feat-a")
    assert str(listed).startswith(str(alias)), "fixture expected the alias spelling"
    found = target_worktree(_bash(container, f"git -C {quote(str(container / 'feat-a'))} status"))
    assert found == listed


class FakeRun:
    """Stands in for `subprocess.run`: records every argv, answers *rc*."""

    def __init__(self, rc: int = 0):
        self.rc = rc
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.rc)


ENV = {"HC_TERMINAL_ID": "t9", "HC_TERMINAL_SOCKET": "/s.sock", "HC_SCOPE": "scope"}


@pytest.fixture
def state_dir(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(root))
    return root / "agent-worktree"


def _git_c(container: Path, name: str, **extra) -> ToolCall:
    return _bash(container, f"git -C {quote(str(container / name))} status", **extra)


def _state_files(state_dir: Path) -> list[Path]:
    return sorted(state_dir.glob("*.json")) if state_dir.is_dir() else []


def test_first_report_labels_and_writes_state(container, state_dir):
    run = FakeRun()
    outcome = report(_git_c(container, "feat-a"), ENV, run=run)
    target = str(_spelling(container, "feat-a"))
    assert outcome is ReportOutcome.LABELLED
    assert run.calls == [["hc", "label", "t9", f"otto.worktree={target}"]]
    [state] = _state_files(state_dir)
    data = json.loads(state.read_text())
    assert (data["worktree"], data["terminal"], data["error"]) == (target, "t9", None)


def test_same_target_again_is_unchanged_and_runs_nothing(container, state_dir):
    report(_git_c(container, "feat-a"), ENV, run=FakeRun())
    run = FakeRun()
    assert report(_git_c(container, "feat-a"), ENV, run=run) is ReportOutcome.UNCHANGED
    assert run.calls == []


def test_new_target_labels_again(container, state_dir):
    report(_git_c(container, "feat-a"), ENV, run=FakeRun())
    run = FakeRun()
    assert report(_git_c(container, "feat-b"), ENV, run=run) is ReportOutcome.LABELLED
    target = str(_spelling(container, "feat-b"))
    assert run.calls == [["hc", "label", "t9", f"otto.worktree={target}"]]


def test_hc_failure_writes_no_state_so_the_next_call_retries(container, state_dir):
    failing = FakeRun(rc=1)
    assert report(_git_c(container, "feat-a"), ENV, run=failing) is ReportOutcome.FAILED
    assert _state_files(state_dir) == []
    retry = FakeRun()
    assert report(_git_c(container, "feat-a"), ENV, run=retry) is ReportOutcome.LABELLED
    assert len(retry.calls) == 1


def test_missing_hc_binary_is_failed_not_raised(container, state_dir):
    def missing(argv, **_kwargs):
        raise FileNotFoundError(argv[0])

    assert report(_git_c(container, "feat-a"), ENV, run=missing) is ReportOutcome.FAILED
    assert _state_files(state_dir) == []


@pytest.mark.parametrize("env", [
    {k: v for k, v in ENV.items() if k != "HC_TERMINAL_ID"},
    {**ENV, "HC_TERMINAL_ID": ""},
], ids=["unset", "empty"])
def test_no_terminal_id_is_skipped(container, state_dir, env):
    run = FakeRun()
    assert report(_git_c(container, "feat-a"), env, run=run) is ReportOutcome.SKIPPED
    assert run.calls == []


def test_subagent_is_skipped(container, state_dir):
    run = FakeRun()
    assert report(_git_c(container, "feat-a", agent_id="x"), ENV, run=run) is ReportOutcome.SKIPPED
    assert run.calls == []


def test_no_target_is_skipped(container, state_dir):
    run = FakeRun()
    assert report(_bash(container, "ls"), ENV, run=run) is ReportOutcome.SKIPPED
    assert run.calls == []


def test_clear_empties_the_label_and_removes_state(container, state_dir):
    report(_git_c(container, "feat-a"), ENV, run=FakeRun())
    assert len(_state_files(state_dir)) == 1
    run = FakeRun()
    clear(ENV, run=run)
    assert run.calls == [["hc", "label", "t9", "otto.worktree="]]
    assert _state_files(state_dir) == []


def test_label_matches_launch_js():
    source = (REPO_ROOT / "ai" / "herdr" / "app" / "mods" / "_lib" / "launch.js").read_text()
    match = re.search(r"^export const LABEL_WORKTREE = '([^']*)';$", source, re.MULTILINE)
    assert match is not None, "LABEL_WORKTREE not found in launch.js"
    assert match.group(1) == pr.agent_worktree.LABEL
