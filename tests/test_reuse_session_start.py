"""Tests for the SessionStart hook's context lines."""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


def _run(rss, repo):
    """Drive main() with the repo fixed and the ceiling scan stubbed out.

    The scan shells out to another script and is orthogonal to what these
    tests assert, so stubbing it keeps them from depending on this repo's
    own marker count.
    """
    with patch.object(rss, "_repo_root", return_value=str(repo)), \
         patch.object(rss, "_ceiling_counts", return_value=None):
        rss.main()


def test_names_the_configured_tracker(rss, tmp_path, monkeypatch, capsys):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(config_dir))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".workbench.yml").write_text(
        "review:\n  issue_tracker:\n    provider: github\n",
    )
    _run(rss, repo)
    assert "Issue tracker: github" in capsys.readouterr().out


def test_says_so_when_no_tracker_is_configured(rss, tmp_path, monkeypatch, capsys):
    """Unconfigured is the state the agent most needs told, so it is not silent."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(config_dir))
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(rss, repo)
    out = capsys.readouterr().out
    assert "Issue tracker: not configured" in out
    assert "review.issue_tracker.provider" in out
