"""Tests for review_issue library."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_issue import (
    IssueContext, IssueProvider, CreatedIssue,
    load_issue_provider, extract_issue_id,
    fetch_issue_context, create_issue, update_issue,
)


# ── extract_issue_id: ported from bats ─────────────────────────────────────


def test_linear_provider_extracts_from_branch():
    assert extract_issue_id("linear", "feat/ABC-123-description") == "ABC-123"


def test_linear_provider_falls_back_to_pr_body():
    assert extract_issue_id("linear", "feat/no-issue-here", "Fixes ABC-456 in production") == "ABC-456"


def test_linear_provider_returns_none_when_no_match(capsys):
    assert extract_issue_id("linear", "feat/no-issue", "no issue here either") is None
    assert "No linear issue ID found in branch 'feat/no-issue' or PR body" in capsys.readouterr().err


def test_jira_provider_same_pattern_as_linear():
    assert extract_issue_id("jira", "fix/PROJ-789-bugfix") == "PROJ-789"


def test_github_provider_extracts_from_closes():
    assert extract_issue_id("github", "feat/something", "Closes #789") == "789"


def test_github_provider_extracts_from_fixes():
    assert extract_issue_id("github", "feat/something", "Fixes #12") == "12"


def test_github_provider_extracts_from_resolves():
    assert extract_issue_id("github", "feat/something", "resolves #1") == "1"


def test_github_provider_returns_none_without_closing_keyword(capsys):
    assert extract_issue_id("github", "feat/something", "see issue #42 for details") is None
    assert "No closes/fixes/resolves keyword found in PR body" in capsys.readouterr().err


def test_none_provider_always_returns_none():
    assert extract_issue_id("none", "feat/ABC-123-description", "Closes #42") is None


def test_linear_provider_takes_first_match_from_branch():
    assert extract_issue_id("linear", "feat/ABC-1-and-DEF-2") == "ABC-1"


# ── extract_issue_id: additional cases ─────────────────────────────────────


def test_unknown_provider_returns_none():
    assert extract_issue_id("bitbucket", "feat/ABC-123", "Closes #1") is None


def test_github_empty_pr_body_returns_none():
    assert extract_issue_id("github", "feat/something") is None


def test_jira_falls_back_to_pr_body():
    assert extract_issue_id("jira", "feat/no-issue", "See PROJ-42 for details") == "PROJ-42"


# ── load_issue_provider ────────────────────────────────────────────────────


def test_load_issue_provider_no_config_returns_linear():
    with patch("os.path.isfile", return_value=False):
        result = load_issue_provider("/nonexistent")
    assert result.name == "linear"
    assert result.options == {}


def test_load_issue_provider_reads_wt_path_config(tmp_path):
    config = tmp_path / ".claude" / "review.yml"
    config.parent.mkdir(parents=True)
    config.write_text("issue_tracker:\n  provider: jira\n  jira_url: https://jira.example.com\n")

    result = MagicMock()
    result.returncode = 0

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        if ".provider" in " ".join(cmd):
            r.stdout = "jira"
        else:
            r.stdout = '{"provider":"jira","jira_url":"https://jira.example.com"}'
        return r

    with patch("subprocess.run", side_effect=fake_run):
        result = load_issue_provider(str(tmp_path))

    assert result.name == "jira"
    assert result.options["jira_url"] == "https://jira.example.com"


def test_load_issue_provider_falls_back_to_workbench_config(tmp_path):
    wt_path = str(tmp_path / "worktree")
    workbench_dir = str(tmp_path / "workbench")
    Path(workbench_dir).mkdir(parents=True)
    review_yml = Path(workbench_dir) / "review.yml"
    review_yml.write_text("issue_tracker:\n  provider: github\n")

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        if ".provider" in " ".join(cmd):
            r.stdout = "github"
        else:
            r.stdout = '{"provider":"github"}'
        return r

    with patch("subprocess.run", side_effect=fake_run), \
         patch.dict("os.environ", {"WORKBENCH_STATE_DIR": workbench_dir}):
        result = load_issue_provider(wt_path)

    assert result.name == "github"


def test_load_issue_provider_yq_failure_falls_back_to_regex(tmp_path):
    config = tmp_path / ".claude" / "review.yml"
    config.parent.mkdir(parents=True)
    config.write_text("issue_tracker:\n  provider: jira\n  jira_url: https://jira.example.com\n")

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("yq not found")

    with patch("subprocess.run", side_effect=fake_run):
        result = load_issue_provider(str(tmp_path))

    assert result.name == "jira"
    assert result.options == {}


# ── fetch_issue_context ────────────────────────────────────────────────────


def test_fetch_issue_context_linear(capsys):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = '{"id":"ABC-123","title":"Fix bug"}'

    with patch("subprocess.run", return_value=mock_result):
        result = fetch_issue_context("linear", "ABC-123")

    assert result.link == ""
    assert result.context == '{"id":"ABC-123","title":"Fix bug"}'
    captured = capsys.readouterr()
    assert "Found Linear issue: ABC-123" in captured.err


def test_fetch_issue_context_github(capsys):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = '{"title":"Bug report","body":"desc"}'

    with patch("subprocess.run", return_value=mock_result):
        result = fetch_issue_context("github", "42", repo="owner/repo")

    assert result.link == "https://github.com/owner/repo/issues/42"
    assert result.context == '{"title":"Bug report","body":"desc"}'
    captured = capsys.readouterr()
    assert "Found GitHub issue: #42" in captured.err


def test_fetch_issue_context_jira(capsys):
    opts = {"jira_url": "https://jira.example.com"}
    result = fetch_issue_context("jira", "PROJ-42", opts=opts)

    assert result.link == "https://jira.example.com/browse/PROJ-42"
    assert result.context == ""
    captured = capsys.readouterr()
    assert "Found Jira issue: PROJ-42" in captured.err


def test_fetch_issue_context_none_returns_empty():
    result = fetch_issue_context("none", "ABC-123")
    assert result.link == ""
    assert result.context == ""


def test_fetch_issue_context_empty_issue_id_returns_empty():
    result = fetch_issue_context("linear", "")
    assert result.link == ""
    assert result.context == ""


def test_fetch_issue_context_none_issue_id_returns_empty():
    result = fetch_issue_context("linear", None)
    assert result.link == ""
    assert result.context == ""


def test_fetch_issue_context_linear_subprocess_failure(capsys):
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with patch("subprocess.run", return_value=mock_result):
        result = fetch_issue_context("linear", "ABC-123")

    assert result.link == ""
    assert result.context == ""
    assert "Linear issue ABC-123 not found or linear CLI unavailable" in capsys.readouterr().err


def test_fetch_issue_context_github_subprocess_failure(capsys):
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with patch("subprocess.run", return_value=mock_result):
        result = fetch_issue_context("github", "42", repo="owner/repo")

    assert result.link == "https://github.com/owner/repo/issues/42"
    assert result.context == ""
    assert "GitHub issue #42 not found in owner/repo" in capsys.readouterr().err


# ── create_issue ──────────────────────────────────────────────────────────


def test_create_issue_linear(capsys):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        if "create" in cmd:
            r.stdout = "Created ENG-456: fix(review): deferred"
        else:
            r.stdout = '{"url": "https://linear.app/team/issue/ENG-456/slug"}'
        return r

    with patch("subprocess.run", side_effect=fake_run):
        result = create_issue("linear", "ENG", "title", "description", parent_id="ENG-123")

    assert result is not None
    assert result.id == "ENG-456"
    assert result.url == "https://linear.app/team/issue/ENG-456/slug"
    create_cmd = calls[0]
    assert "--team" in create_cmd
    assert "--parent" in create_cmd
    assert "ENG-123" in create_cmd
    assert "--description-file" in create_cmd


def test_create_issue_linear_no_parent(capsys):
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        if "create" in cmd:
            r.stdout = "Created ENG-456"
        else:
            r.stdout = '{"url": "https://linear.app/team/issue/ENG-456/slug"}'
        return r

    with patch("subprocess.run", side_effect=fake_run):
        result = create_issue("linear", "ENG", "title", "description")

    assert result is not None
    assert result.id == "ENG-456"


def test_create_issue_linear_failure():
    r = MagicMock()
    r.returncode = 1
    r.stdout = ""

    with patch("subprocess.run", return_value=r):
        result = create_issue("linear", "ENG", "title", "description")

    assert result is None


def test_create_issue_github(capsys):
    r = MagicMock()
    r.returncode = 0
    r.stdout = "https://github.com/owner/repo/issues/42\n"

    with patch("subprocess.run", return_value=r):
        result = create_issue("github", "", "title", "description", repo="owner/repo")

    assert result is not None
    assert result.id == "#42"
    assert result.url == "https://github.com/owner/repo/issues/42"


def test_create_issue_unsupported_provider(capsys):
    result = create_issue("jira", "PROJ", "title", "description")
    assert result is None


# ── update_issue ──────────────────────────────────────────────────────────


def test_update_issue_linear(capsys):
    r = MagicMock()
    r.returncode = 0
    r.stdout = ""

    with patch("subprocess.run", return_value=r):
        ok = update_issue("linear", "ENG-456", "new description")

    assert ok is True


def test_update_issue_linear_failure():
    r = MagicMock()
    r.returncode = 1
    r.stdout = ""

    with patch("subprocess.run", return_value=r):
        ok = update_issue("linear", "ENG-456", "new description")

    assert ok is False


def test_update_issue_github(capsys):
    r = MagicMock()
    r.returncode = 0
    r.stdout = ""

    with patch("subprocess.run", return_value=r):
        ok = update_issue("github", "#42", "new description", repo="owner/repo")

    assert ok is True


def test_update_issue_unsupported_provider(capsys):
    ok = update_issue("jira", "PROJ-42", "description")
    assert ok is False
