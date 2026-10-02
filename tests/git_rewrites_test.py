"""Tests for `git.rewrites` and the global `post-rewrite` hook that feeds it.

The hook tests drive real amends and rebases through the real hook, because
what is under test is that git's own report reaches the log: a hand-written log
would only test the parser.
"""

import os
import shutil
import subprocess
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


def _record(common, text):
    git.rewrites.record(common, git.rewrites.parse(text))


class TestParse:
    def test_reads_git_lines_and_ignores_the_extra_field(self):
        rewrites = git.rewrites.parse(f"{A} {B}\n{B} {C} extra\n\nlone\n")
        assert rewrites == [git.rewrites.Rewrite(A, B), git.rewrites.Rewrite(B, C)]

    def test_keeps_a_commit_mapped_onto_itself_for_the_drop_rule(self):
        """`drops` reads each line against the one before it."""
        assert git.rewrites.parse(f"{A} {A}\n") == [git.rewrites.Rewrite(A, A)]

    def test_only_lines_git_could_write_about_a_changed_commit_are_informative(self):
        assert git.rewrites.Rewrite(A, B).informative
        assert git.rewrites.Rewrite("a" * 64, "b" * 64).informative
        assert not git.rewrites.Rewrite(A, A).informative
        assert not git.rewrites.Rewrite(A[:12], B).informative
        assert not git.rewrites.Rewrite("not-a-sha", B).informative


def test_command_for_prefers_the_more_specific_sha_match():
    """Two `done` entries can each be a startswith-match for the same commit
    when one abbreviated sha happens to be a prefix of another — the longer,
    more specific one is what should win, not whichever the dict iterates to
    first.
    """
    commit = "ab12cdef1234"
    commands = {"ab12": "pick", "ab12cdef1234": "edit"}

    assert git.rewrites.command_for(commands, commit) == "edit"


class TestDrops:
    """git maps a dropped commit onto its predecessor; that is not a rewrite."""

    ONTO, D = "0" * 40, "d" * 40

    def test_a_pick_mapped_onto_the_new_commit_before_it_was_dropped(self):
        rewrites = git.rewrites.parse(f"{A} {C}\n{B} {C}\n")
        commands = {A: "pick", B: "pick"}
        assert git.rewrites.drops(rewrites, self.ONTO, commands) == [git.rewrites.Rewrite(B, C)]

    def test_a_first_commit_mapped_onto_onto_was_dropped(self):
        rewrites = git.rewrites.parse(f"{A} {self.ONTO}\n")
        assert git.rewrites.drops(rewrites, self.ONTO, {A: "pick"}) == rewrites

    def test_a_fixup_folded_into_its_predecessor_is_a_rewrite(self):
        rewrites = git.rewrites.parse(f"{A} {C}\n{B} {C}\n")
        assert git.rewrites.drops(rewrites, self.ONTO, {A: "pick", B: "fixup"}) == []

    def test_a_self_mapped_line_still_sets_the_predecessor(self):
        """Filter it out before the rule and the drop after it reads as a rewrite."""
        rewrites = git.rewrites.parse(f"{A} {A}\n{B} {A}\n")
        assert git.rewrites.drops(rewrites, self.ONTO, {A: "pick", B: "pick"}) == [
            git.rewrites.Rewrite(B, A)]


class TestSplit:
    ONTO = "0" * 40

    def test_a_pair_reported_twice_is_two_lines_and_only_one_is_the_drop(self):
        """By position, not by value: the repeat maps onto its predecessor, the
        first does not, and filtering by value would remove both."""
        rewrites = [git.rewrites.Rewrite(A, B), git.rewrites.Rewrite(A, B)]
        kept, dropped = git.rewrites.split(rewrites, self.ONTO, {A: "pick"})
        assert (kept, dropped) == ([rewrites[0]], [rewrites[1]])

    def test_a_line_whose_commit_the_todo_does_not_name_is_neither(self):
        rewrites = git.rewrites.parse(f"{A} {self.ONTO}\n{B} {C}\n")
        assert git.rewrites.split(rewrites, self.ONTO, {B: "pick"}) == (
            [rewrites[1]], [])

    def test_the_rest_are_rewrites(self):
        rewrites = git.rewrites.parse(f"{A} {C}\n{B} {C}\n")
        assert git.rewrites.split(rewrites, self.ONTO, {A: "pick", B: "fixup"}) == (
            rewrites, [])


class TestDoneCommands:
    def test_an_unreadable_file_is_not_an_empty_one(self, tmp_path):
        assert git.rewrites.done_commands(tmp_path) is None
        (tmp_path / "done").write_text("")
        assert git.rewrites.done_commands(tmp_path) == {}


class TestRecordAndLoad:
    def test_a_line_appended_to_a_log_missing_its_last_newline_stays_whole(self, repo):
        common = git.rewrites.common_dir(repo)
        (common / git.rewrites.LOG_NAME).write_text(f"{A} {B}")
        _record(common, f"{B} {C}\n")
        assert git.rewrites.load(repo) == {A: [B], B: [C]}

    def test_a_recorded_rewrite_loads_back(self, repo):
        _record(git.rewrites.common_dir(repo), f"{A} {B}\n")
        assert git.rewrites.load(repo) == {A: [B]}

    def test_a_commit_rewritten_twice_keeps_both_answers_in_order(self, repo):
        common = git.rewrites.common_dir(repo)
        _record(common, f"{A} {B}\n")
        _record(common, f"{A} {C}\n")
        assert git.rewrites.load(repo) == {A: [B, C]}

    def test_uninformative_lines_are_not_written(self, repo):
        common = git.rewrites.common_dir(repo)
        _record(common, f"{A} {A}\nnot-a-sha {B}\n")
        assert not (common / git.rewrites.LOG_NAME).exists()

    def test_no_log_loads_as_nothing_recorded(self, repo):
        assert git.rewrites.load(repo) == {}

    def test_a_worktree_shares_its_repository_log(self, repo, tmp_path):
        """A commit belongs to the repository, not to the checkout that rewrote it."""
        linked = tmp_path / "linked"
        git_in(repo, "worktree", "add", "-q", "-b", "other", str(linked))
        _record(git.rewrites.common_dir(linked), f"{A} {B}\n")
        assert git.rewrites.common_dir(linked) == git.rewrites.common_dir(repo)
        assert git.rewrites.load(repo) == {A: [B]}

    def test_the_log_is_trimmed_to_its_newest_entries(self, repo, monkeypatch):
        monkeypatch.setattr(git.rewrites, "_MAX_LINES", 4)
        monkeypatch.setattr(git.rewrites, "_KEEP_LINES", 2)
        monkeypatch.setattr(git.rewrites, "_MAX_LINE_BYTES", 82)
        common = git.rewrites.common_dir(repo)
        olds = [f"{i:040x}" for i in range(1, 7)]
        for old in olds:
            _record(common, f"{old} {B}\n")
        loaded = git.rewrites.load(repo)
        assert olds[-1] in loaded
        assert olds[0] not in loaded
        assert len(loaded) <= 4
        assert not list(common.glob(f"{git.rewrites.LOG_NAME}.*.tmp"))

    def test_a_damaged_log_is_still_trimmed(self, repo, monkeypatch):
        """A non-ASCII byte must not make every trim raise and the log grow."""
        monkeypatch.setattr(git.rewrites, "_KEEP_LINES", 2)
        path = git.rewrites.common_dir(repo) / git.rewrites.LOG_NAME
        olds = [f"{i:040x}" for i in range(1, 5)]
        lines = [f"{old} {B}\n".encode() for old in olds]
        path.write_bytes(lines[0] + b"\xff\xfe\n" + b"".join(lines[1:]))

        git.rewrites._trim(path)

        assert git.rewrites.load(repo) == {olds[2]: [B], olds[3]: [B]}


class TestPostRewriteHook:
    def test_step_global_hooks_installs_the_hook(self, tmp_path):
        """Dropping the install line would silently leave only patch matching."""
        hooks = tmp_path / "installed"
        env = {**os.environ, "HOME": str(tmp_path),
               "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
               "GIT_CONFIG_SYSTEM": "/dev/null"}
        run_checked(
            ["bash", "-c",
             f'. "{REPO_ROOT}/lib/ui.sh"; . "{REPO_ROOT}/git/steps.sh"; '
             f'GIT_HOOKS_DIR="{hooks}" GIT_HOOKS_SRC_DIR="{REPO_ROOT}/git/hooks" '
             f'WORKBENCH_DIR="{REPO_ROOT}" WORKBENCH_STABLE_DIR="{REPO_ROOT}" '
             "step_global_hooks"],
            cwd=tmp_path, env=env,
        )
        assert (hooks / "post-rewrite").is_symlink()
        assert (hooks / "post-rewrite").resolve() == HOOK.resolve()

    def test_a_git_that_echoes_unknown_options_still_records(self, repo, tmp_path):
        """git older than 2.31 prints `--path-format=absolute` back as an answer.

        The hook is run by hand: git puts its own exec path ahead of `PATH` for
        a hook it runs, which would find the real git instead of the stand-in.
        """
        shim_dir = tmp_path / "oldgit"
        shim_dir.mkdir()
        shim = shim_dir / "git"
        shim.write_text(
            "#!/usr/bin/env bash\n"
            'for a in "$@"; do\n'
            '  [[ "$a" == --path-format=* ]] && echo "$a"\n'
            "done\n"
            f'exec "{shutil.which("git")}" "$@"\n'
        )
        shim.chmod(0o755)
        env = {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"}

        subprocess.run(
            ["bash", str(HOOK), "amend"], cwd=repo, env=env, input=f"{A} {B}\n",
            text=True, check=True, timeout=60,
        )

        assert git.rewrites.load(repo) == {A: [B]}

    def test_a_relative_link_that_resolves_nowhere_records_nothing_and_breaks_nothing(
        self, repo, tmp_path, live_git_hooks,
    ):
        """`readlink` returns the link's text, so a relative one is resolved
        against the repository, not the link's own directory."""
        hooks = tmp_path / "hooks"
        moved = hooks / "moved" / "git" / "hooks"
        moved.mkdir(parents=True)
        (moved / "post-rewrite").write_text(HOOK.read_text())
        (moved / "post-rewrite").chmod(0o755)
        (hooks / "post-rewrite").symlink_to("moved/git/hooks/post-rewrite")
        git_in(repo, "config", "core.hooksPath", str(hooks))
        _commit(repo, "fix.txt")
        assert not (repo / "moved").exists()
        result = run_checked(
            ["git", "commit", "-q", "--no-verify", "--amend", "-m", "reworded"],
            cwd=repo, check=False,
        )
        assert result.returncode == 0
        assert result.stderr == ""
        assert git.rewrites.load(repo) == {}

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

    def test_a_skipped_commit_is_not_recorded_as_its_predecessor(self, hooked):
        """git maps a dropped commit onto the one before it. Recorded, that
        would follow a dropped fix to an unrelated commit and clear its hold."""
        git_in(hooked, "checkout", "-q", "-b", "feature")
        dropped = _commit(hooked, "base.txt", "feature\n")
        kept = _commit(hooked, "kept.txt")
        git_in(hooked, "checkout", "-q", "main")
        _commit(hooked, "base.txt", "upstream\n")
        git_in(hooked, "checkout", "-q", "feature")
        assert run_checked(["git", "rebase", "-q", "main"], cwd=hooked,
                           check=False).returncode != 0
        run_checked(["git", "-c", "core.editor=true", "rebase", "--skip"], cwd=hooked)

        assert git.rewrites.load(hooked) == {kept: [_full(hooked)]}
        assert dropped not in git.rewrites.load(hooked)

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
