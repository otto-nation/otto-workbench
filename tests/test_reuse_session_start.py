"""Tests for the SessionStart hook's context lines."""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from config import workbench_config  # noqa: E402

ISSUE_TRACKER_RULE = REPO_ROOT / "ai" / "guidelines" / "rules" / "issue-tracker.md"


def _run(rss, repo):
    """Drive main() with the repo fixed and the ceiling scan stubbed out.

    The scan shells out to another script and is orthogonal to what these
    tests assert, so stubbing it keeps them from depending on this repo's
    own marker count.
    """
    with patch.object(rss, "_repo_root", return_value=str(repo)), \
         patch.object(rss, "_ceiling_counts", return_value=None):
        rss.main()


def test_names_the_configured_tracker(rss, tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".workbench.yml").write_text(
        "issues:\n  provider: github\n",
    )
    _run(rss, repo)
    assert "Issue tracker: github" in capsys.readouterr().out


def test_says_so_when_no_tracker_is_configured(rss, tmp_path, capsys):
    """Unconfigured is the state the agent most needs told, so it is not silent."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(rss, repo)
    out = capsys.readouterr().out
    assert "Issue tracker: not configured" in out
    assert workbench_config.ISSUE_PROVIDER_KEY in out


def test_the_rule_quotes_both_lines_the_hook_emits(rss, tmp_path):
    """The rule tells the agent to read a line this hook owns the wording of.

    It quotes both states verbatim, so a reworded hook would otherwise point
    the agent at a line that no longer exists. Reword the rule to match, not
    the other way round.
    """
    configured = tmp_path / "configured"
    configured.mkdir()
    (configured / ".workbench.yml").write_text(
        "issues:\n  provider: github\n",
    )
    unconfigured = tmp_path / "unconfigured"
    unconfigured.mkdir()

    rule = ISSUE_TRACKER_RULE.read_text()
    assert rss._issues_line(str(configured)) in rule
    assert rss._issues_line(str(unconfigured)) in rule


class TestWhereTheSessionStarted:
    """Which directory the hook reads the repo out of.

    Claude Code roots a session wherever it was launched, and for a bare-repo
    checkout that is routinely the container rather than a worktree. These
    drive `_repo_root` for real rather than patching it, because the bug they
    cover was in `_repo_root` itself: it returned None at a container, and the
    hook then emitted nothing at all.
    """

    def test_a_worktree_rooted_session_gets_the_tracker(self, rss, container, monkeypatch, capsys):
        worktree = container / "main"
        (worktree / ".workbench.yml").write_text("issues:\n  provider: github\n")
        monkeypatch.chdir(worktree)
        with patch.object(rss, "_ceiling_counts", return_value=None):
            rss.main()
        assert "Issue tracker: github" in capsys.readouterr().out

    def test_a_container_rooted_session_gets_the_same_tracker(
        self, rss, container, monkeypatch, capsys,
    ):
        """The regression. A container answered nothing, silently.

        Silence is what made it worth fixing: a session that never learned the
        tracker is indistinguishable from one that did, until it files
        somewhere wrong.
        """
        (container / "main" / ".workbench.yml").write_text(
            "issues:\n  provider: github\n",
        )
        monkeypatch.chdir(container)
        with patch.object(rss, "_ceiling_counts", return_value=None):
            rss.main()
        assert "Issue tracker: github" in capsys.readouterr().out

    def test_the_config_comes_from_the_worktree_not_the_container(
        self, rss, container, monkeypatch, capsys,
    ):
        """Resolving to the checkout is what makes the committed config visible.

        Handing the container straight to the config loader does not fail, it
        answers differently: `container_dir` of a container is None, so the
        container's own file is read as the project scope and the worktree's
        committed one is never seen. A container-scope file that disagrees is
        the way to tell the two apart.
        """
        (container / "main" / ".workbench.yml").write_text(
            "issues:\n  provider: linear\n",
        )
        (container / ".workbench.yml").write_text("issues:\n  provider: github\n")
        monkeypatch.chdir(container)
        with patch.object(rss, "_ceiling_counts", return_value=None):
            rss.main()
        assert "Issue tracker: linear" in capsys.readouterr().out

    def test_a_container_with_no_worktree_stays_silent(
        self, rss, tmp_path, monkeypatch, capsys,
    ):
        """Exit 1 from resolve-worktree is a refusal, not a path to guess at."""
        from conftest import run_checked, seed_repo

        seed = seed_repo(tmp_path / "seed")
        root = tmp_path / "empty-container"
        run_checked(["git", "clone", "-q", "--bare", str(seed), str(root / ".git")])
        monkeypatch.chdir(root)
        with patch.object(rss, "_ceiling_counts", return_value=None):
            rss.main()
        assert "Issue tracker" not in capsys.readouterr().out

    def test_outside_a_repo_stays_silent(self, rss, tmp_path, monkeypatch, capsys):
        plain = tmp_path / "plain"
        plain.mkdir()
        monkeypatch.chdir(plain)
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
        with patch.object(rss, "_ceiling_counts", return_value=None):
            rss.main()
        assert "Issue tracker" not in capsys.readouterr().out
