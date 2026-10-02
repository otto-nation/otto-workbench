"""Tests for `git.rewrites` and the global `post-rewrite` hook that feeds it.

The hook tests drive real amends and rebases through the real hook, because
what is under test is that git's own report reaches the log: a hand-written log
would only test the parser.
"""

import io
import sys

from conftest import REPO_ROOT, git_in, git_out, run_checked

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

import git.rewrites  # noqa: E402

HOOK = REPO_ROOT / "git" / "hooks" / "post-rewrite"
A, B, C = "a" * 40, "b" * 40, "c" * 40


def _full(work, rev="HEAD") -> str:
    return git_out(work, "rev-parse", rev).strip()


def _commit(work, name, content=None) -> str:
    (work / name).write_text(content if content is not None else f"{name}\n")
    git_in(work, "add", "-A")
    git_in(work, "commit", "-q", "--no-verify", "-m", f"add {name}")
    return _full(work)


@pytest.fixture
def repo(tmp_path):
    work = tmp_path / "work"
    run_checked(["git", "init", "-q", "-b", "main", str(work)])
    git_in(work, "config", "user.email", "t@example.com")
    git_in(work, "config", "user.name", "Test")
    _commit(work, "base.txt")
    return work


@pytest.fixture
def hooked(repo, record_rewrites):
    """*repo* with only the workbench `post-rewrite` hook installed."""
    return record_rewrites(repo)


class TestParse:
    def test_reads_git_lines_and_ignores_the_extra_field(self):
        rewrites = git.rewrites.parse(f"{A} {B}\n{B} {C} extra\n")
        assert [(r.old, r.new) for r in rewrites] == [(A, B), (B, C)]

    def test_drops_lines_git_would_never_write(self):
        text = f"{A}\nnot-a-sha {B}\n{A} {A}\n{A[:12]} {B}\n"
        assert git.rewrites.parse(text) == []

    def test_accepts_sha256_object_names(self):
        old, new = "a" * 64, "b" * 64
        assert [(r.old, r.new) for r in git.rewrites.parse(f"{old} {new}\n")] == [(old, new)]


class TestRecordAndLoad:
    def test_a_recorded_rewrite_loads_back(self, repo):
        git.rewrites.record(git.rewrites.log_path(repo).parent, f"{A} {B}\n")
        assert git.rewrites.load(repo) == {A: [B]}

    def test_a_commit_rewritten_twice_keeps_both_answers_in_order(self, repo):
        common = git.rewrites.log_path(repo).parent
        git.rewrites.record(common, f"{A} {B}\n")
        git.rewrites.record(common, f"{A} {C}\n")
        assert git.rewrites.load(repo) == {A: [B, C]}

    def test_no_log_loads_as_nothing_recorded(self, repo):
        assert git.rewrites.load(repo) == {}

    def test_a_worktree_shares_its_repository_log(self, repo, tmp_path):
        """A commit belongs to the repository, not to the checkout that rewrote it."""
        linked = tmp_path / "linked"
        git_in(repo, "worktree", "add", "-q", "-b", "other", str(linked))
        git.rewrites.record(git.rewrites.log_path(linked).parent, f"{A} {B}\n")
        assert git.rewrites.load(repo) == {A: [B]}

    def test_the_log_is_trimmed_to_its_newest_entries(self, repo, monkeypatch):
        monkeypatch.setattr(git.rewrites, "_MAX_LINES", 4)
        monkeypatch.setattr(git.rewrites, "_KEEP_LINES", 2)
        monkeypatch.setattr(git.rewrites, "_MAX_LINE_BYTES", 82)
        common = git.rewrites.log_path(repo).parent
        olds = [f"{i:040x}" for i in range(1, 7)]
        for old in olds:
            git.rewrites.record(common, f"{old} {B}\n")
        loaded = git.rewrites.load(repo)
        assert olds[-1] in loaded
        assert olds[0] not in loaded
        assert len(loaded) <= 4
        assert not list(common.glob(f"{git.rewrites.LOG_NAME}.*.tmp"))

    def test_main_never_fails_the_hook(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO(f"{A} {B}\n"))
        missing = tmp_path / "no" / "such" / "dir"
        assert git.rewrites.main(["--git-dir", str(missing)]) == 0
        assert "could not record" in capsys.readouterr().err


class TestPostRewriteHook:
    def test_an_amend_is_recorded(self, hooked):
        old = _commit(hooked, "fix.txt")
        git_in(hooked, "commit", "-q", "--no-verify", "--amend", "-m", "reworded")
        assert git.rewrites.load(hooked) == {old: [_full(hooked)]}

    def test_a_rebase_records_every_pick(self, hooked):
        git_in(hooked, "checkout", "-q", "-b", "feature")
        first = _commit(hooked, "one.txt")
        second = _commit(hooked, "two.txt")
        git_in(hooked, "checkout", "-q", "main")
        _commit(hooked, "upstream.txt")
        git_in(hooked, "checkout", "-q", "feature")
        git_in(hooked, "rebase", "-q", "main")
        assert git.rewrites.load(hooked) == {
            first: [_full(hooked, "HEAD~1")], second: [_full(hooked)],
        }

    def test_a_fixup_is_recorded_against_the_commit_it_folded_into(self, hooked):
        git_in(hooked, "checkout", "-q", "-b", "feature")
        target = _commit(hooked, "one.txt")
        (hooked / "one.txt").write_text("one, fixed\n")
        git_in(hooked, "commit", "-q", "--no-verify", "-am", "fixup! add one.txt")
        fixup = _full(hooked)
        git_in(hooked, "-c", "sequence.editor=true", "rebase", "-q", "-i",
               "--autosquash", "main")
        folded = _full(hooked)
        assert git.rewrites.load(hooked) == {target: [folded], fixup: [folded]}

    def test_the_repo_local_hook_still_runs_with_the_same_input(self, hooked):
        """Global hooksPath hides .git/hooks; the global hook hands it back."""
        local = hooked / ".git" / "hooks" / "post-rewrite"
        seen = hooked / ".git" / "local-hook-saw"
        local.write_text(f'#!/usr/bin/env bash\ncat > "{seen}"\necho "$1" >> "{seen}"\n')
        local.chmod(0o755)
        old = _commit(hooked, "fix.txt")
        git_in(hooked, "commit", "-q", "--no-verify", "--amend", "-m", "reworded")
        assert seen.read_text().splitlines() == [f"{old} {_full(hooked)}", "amend"]

    def test_a_workbench_that_moved_records_nothing_and_breaks_nothing(
        self, repo, tmp_path, live_git_hooks,
    ):
        """A stale link must cost the record, never the operator's amend."""
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        stale = tmp_path / "gone" / "git" / "hooks" / "post-rewrite"
        stale.parent.mkdir(parents=True)
        stale.write_text(HOOK.read_text())
        stale.chmod(0o755)
        (hooks / "post-rewrite").symlink_to(stale)
        git_in(repo, "config", "core.hooksPath", str(hooks))
        _commit(repo, "fix.txt")
        result = run_checked(
            ["git", "commit", "-q", "--no-verify", "--amend", "-m", "reworded"],
            cwd=repo, check=False,
        )
        assert result.returncode == 0
        assert result.stderr == ""
        assert git.rewrites.load(repo) == {}
