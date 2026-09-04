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
        "issue_tracker:\n  provider: github\n",
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
        "issue_tracker:\n  provider: github\n",
    )
    unconfigured = tmp_path / "unconfigured"
    unconfigured.mkdir()

    rule = ISSUE_TRACKER_RULE.read_text()
    assert rss._issue_tracker_line(str(configured)) in rule
    assert rss._issue_tracker_line(str(unconfigured)) in rule
