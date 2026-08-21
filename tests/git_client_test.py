"""Tests for the git client.

Against a real repo rather than a mocked `subprocess`: every read here is a
claim about what git actually answers, and a mock that returns the string the
test author expected would pass whether or not the flag combination is right.
The one exception is the argv builder, which is pure and is asserted directly.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git_client  # noqa: E402
import proc  # noqa: E402
import timeouts  # noqa: E402

from conftest import init_worktree  # noqa: E402


def _commit(path: Path, name: str = "f.txt", content: str = "one") -> str:
    """Write a file and commit it, returning the resulting SHA."""
    (path / name).write_text(content)
    git_client.run("add", "--", name, cwd=path)
    git_client.run(
        "commit", "-m", f"add {name}", cwd=path,
        config={"user.email": "t@example.com", "user.name": "T"},
    )
    return git_client.head_sha(cwd=path)


@pytest.fixture
def repo(tmp_path) -> Path:
    return init_worktree(tmp_path)


# ── argv ────────────────────────────────────────────────────────────────────


def test_argv_puts_config_before_the_subcommand():
    assert git_client._argv(("log",), {"core.editor": "true"}) == [
        "git", "-c", "core.editor=true", "log",
    ]


def test_argv_defaults_quote_path_off_for_path_listing():
    assert git_client._argv(("ls-files",), None) == [
        "git", "-c", "core.quotePath=false", "ls-files",
    ]


def test_argv_leaves_other_subcommands_alone():
    assert git_client._argv(("rev-parse", "HEAD"), None) == ["git", "rev-parse", "HEAD"]


def test_argv_lets_a_caller_override_the_quote_path_default():
    assert git_client._argv(("diff",), {"core.quotePath": "true"}) == [
        "git", "-c", "core.quotePath=true", "diff",
    ]


# ── Timeout policy ──────────────────────────────────────────────────────────


def test_a_metadata_read_takes_the_local_tier():
    assert git_client._timeout_for(("rev-parse", "HEAD")) == timeouts.LOCAL


def test_a_fetch_takes_the_transfer_tier():
    """--unshallow moves the whole history, so latency is not the bound."""
    assert git_client._timeout_for(("fetch", "--unshallow")) == timeouts.TRANSFER


@pytest.mark.parametrize("subcommand", ["worktree", "commit", "push"])
def test_input_and_hook_bound_subcommands_run_unbounded(subcommand):
    """A fixed bound here reports a large repo, or a slow hook, as a breakage."""
    assert git_client._timeout_for((subcommand, "-m", "x")) is timeouts.UNBOUNDED


def test_an_empty_argv_still_resolves_a_tier():
    assert git_client._timeout_for(()) == timeouts.LOCAL


def test_run_takes_no_timeout_from_its_caller():
    """The bound is the client's to decide, so there is nothing to override."""
    with pytest.raises(TypeError):
        git_client.run("rev-parse", "HEAD", timeout=1)


def test_an_expired_bound_arrives_as_a_failed_result(repo, monkeypatch):
    """Every read degrades to its default, so no call site needs a handler."""
    monkeypatch.setattr(git_client, "_timeout_for", lambda args: 0.001)
    r = git_client.run("log", "-1", cwd=repo)
    assert r.returncode == proc.TIMEOUT_RETURNCODE
    assert "timed out after" in r.stderr
    assert git_client.out("log", "-1", cwd=repo, default="unknown") == "unknown"


# ── Runner ──────────────────────────────────────────────────────────────────


def test_run_reports_a_failure_without_raising(repo):
    r = git_client.run("rev-parse", "--verify", "nope", cwd=repo)
    assert not r.ok
    assert r.stderr


def test_run_carries_stderr_so_a_caller_can_name_the_cause(repo):
    r = git_client.run("checkout", "missing-branch", cwd=repo)
    assert "missing-branch" in r.combined_output


def test_out_strips_and_returns_stdout(repo):
    _commit(repo)
    assert git_client.out("log", "-1", "--format=%s", cwd=repo) == "add f.txt"


def test_out_falls_back_to_the_default_on_failure(repo):
    assert git_client.out("rev-parse", "nope", cwd=repo, default="unknown") == "unknown"


def test_ok_reads_the_exit_code(repo):
    _commit(repo)
    assert git_client.ok("diff", "--quiet", cwd=repo)
    (repo / "f.txt").write_text("two")
    assert not git_client.ok("diff", "--quiet", cwd=repo)


def test_lines_drops_blanks_and_returns_a_list(repo):
    _commit(repo, "a.txt")
    _commit(repo, "b.txt")
    assert sorted(git_client.lines("ls-files", cwd=repo)) == ["a.txt", "b.txt"]


def test_lines_is_empty_when_the_command_failed(repo):
    assert git_client.lines("ls-files", "--bogus-flag", cwd=repo) == []


def test_a_path_listing_read_returns_a_usable_pathspec(repo):
    """The reason `core.quotePath=false` is a property of the subcommand.

    With git's default escaping this name comes back as "\\303\\251.txt", which
    `git add` then resolves to nothing — the fix is staged as an empty commit
    and reported as applied.
    """
    (repo / "é.txt").write_text("x")
    listed = git_client.lines("ls-files", "--others", "--exclude-standard", cwd=repo)
    assert listed == ["é.txt"]
    assert git_client.run("add", "--", *listed, cwd=repo).ok
    assert git_client.lines("diff", "--cached", "--name-only", cwd=repo) == ["é.txt"]


# ── Reads ───────────────────────────────────────────────────────────────────


def test_head_sha_full_and_short(repo):
    sha = _commit(repo)
    assert len(sha) == 40
    short = git_client.head_sha(cwd=repo, short=True)
    assert sha.startswith(short)


def test_head_sha_is_empty_before_the_first_commit(repo):
    assert git_client.head_sha(cwd=repo) == ""


def test_current_branch_names_the_checked_out_branch(repo):
    _commit(repo)
    assert git_client.current_branch(cwd=repo) == "main"


def test_current_branch_answers_head_when_detached(repo):
    sha = _commit(repo)
    git_client.run("checkout", "--detach", sha, cwd=repo)
    assert git_client.current_branch(cwd=repo) == "HEAD"


def test_current_branch_is_empty_outside_a_repo(tmp_path):
    assert git_client.current_branch(cwd=tmp_path) == ""


def test_is_dirty_sees_an_untracked_file(repo):
    _commit(repo)
    assert not git_client.is_dirty(cwd=repo)
    (repo / "new.txt").write_text("x")
    assert git_client.is_dirty(cwd=repo)


def test_is_dirty_sees_a_staged_change(repo):
    _commit(repo)
    (repo / "f.txt").write_text("two")
    git_client.run("add", "--", "f.txt", cwd=repo)
    assert git_client.is_dirty(cwd=repo)


def test_commit_exists_distinguishes_a_real_sha(repo):
    sha = _commit(repo)
    assert git_client.commit_exists(sha, cwd=repo)
    assert not git_client.commit_exists("0" * 40, cwd=repo)


def test_commit_exists_rejects_a_non_commit_object(repo):
    _commit(repo)
    blob = git_client.out("rev-parse", "HEAD:f.txt", cwd=repo)
    assert blob
    assert not git_client.commit_exists(blob, cwd=repo)
