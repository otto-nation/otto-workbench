"""Tests for review_issue library."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_issue import (
    IssueContext, IssueProviderInfo, CreatedIssue,
    load_issue_provider, ensure_issue_provider, extract_issue_id,
    needs_team_key, fetch_issue_context, create_issue, update_issue,
)
import workbench_config


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


def test_load_issue_provider_is_unresolved_without_config(tmp_path):
    result = load_issue_provider(str(tmp_path))
    assert result.name == ""
    assert result.resolved is False
    assert result.options == {}


def test_load_issue_provider_reads_the_project_config(tmp_path):
    (tmp_path / ".workbench.yml").write_text(
        "review:\n  issue_tracker:\n    provider: github\n    team: ENG\n",
    )
    result = load_issue_provider(str(tmp_path))
    assert result.name == "github"
    assert result.resolved is True
    assert result.options["team"] == "ENG"


def test_load_issue_provider_falls_back_to_the_global_config(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(config_dir))
    (config_dir / "config.yml").write_text(
        "review:\n  issue_tracker:\n    provider: jira\n    jira_url: https://j.example\n",
    )
    result = load_issue_provider(str(tmp_path / "elsewhere"))
    assert result.name == "jira"
    assert result.options["jira_url"] == "https://j.example"


# ── needs_team_key ──────────────────────────────────────────────────────────


def test_needs_team_key_is_true_for_linear():
    assert needs_team_key("linear") is True


def test_needs_team_key_is_false_for_github():
    """gh issue create takes a repo, not a team."""
    assert needs_team_key("github") is False


def test_needs_team_key_is_false_for_jira():
    """Jira creation is not automated, so a team key is not what blocks it."""
    assert needs_team_key("jira") is False


# ── ensure_issue_provider ───────────────────────────────────────────────────


def test_ensure_issue_provider_returns_a_declared_provider_without_asking(tmp_path):
    (tmp_path / ".workbench.yml").write_text(
        "review:\n  issue_tracker:\n    provider: github\n",
    )
    with patch("review_issue.prompt.ask") as asked:
        result = ensure_issue_provider(str(tmp_path))
    assert result.name == "github"
    asked.assert_not_called()


def test_ensure_issue_provider_warns_and_stays_unresolved_without_a_tty(tmp_path, capsys):
    with patch("review_issue.prompt.interactive", return_value=False):
        result = ensure_issue_provider(str(tmp_path))
    assert result.resolved is False
    err = capsys.readouterr().err
    assert workbench_config.ISSUE_PROVIDER_KEY in err
    assert str(tmp_path) in err


def test_ensure_issue_provider_names_both_scopes_without_a_tty(tmp_path, capsys):
    """A CI user cannot answer the question, so tell them both files it can live in."""
    with patch("review_issue.prompt.interactive", return_value=False):
        ensure_issue_provider(str(tmp_path))
    err = capsys.readouterr().err
    assert workbench_config.PROJECT_CONFIG_NAME in err
    assert str(workbench_config.global_config_path()) in err


def test_ensure_issue_provider_reports_a_broken_project_config(tmp_path, capsys):
    """A typo is not an unset provider — recording over it would be shadowed."""
    (tmp_path / ".workbench.yml").write_text(
        "review:\n  issue_tracker:\n    provider: gihtub\n",
    )
    with patch("review_issue.prompt.interactive", return_value=True), \
         patch("review_issue.prompt.ask") as asked:
        result = ensure_issue_provider(str(tmp_path))
    assert result.resolved is False
    asked.assert_not_called()
    err = capsys.readouterr().err
    assert str(tmp_path / ".workbench.yml") in err
    assert "gihtub" in err


def test_ensure_issue_provider_still_prompts_when_the_config_is_merely_absent(tmp_path):
    """The strict check must not turn every unconfigured repo into a report."""
    with patch("review_issue.prompt.interactive", return_value=True), \
         patch("review_issue.prompt.ask", side_effect=["github", "repo"]) as asked:
        result = ensure_issue_provider(str(tmp_path))
    assert result.name == "github"
    assert asked.call_count == 2


def test_ensure_issue_provider_records_the_answer_for_the_repo(tmp_path):
    with patch("review_issue.prompt.interactive", return_value=True), \
         patch("review_issue.prompt.ask", side_effect=["github", "repo"]):
        result = ensure_issue_provider(str(tmp_path))
    assert result.name == "github"
    assert "provider: github" in (tmp_path / ".workbench.yml").read_text()
    assert not (tmp_path / "workbench-config" / "config.yml").exists()


def test_ensure_issue_provider_records_the_answer_for_all_repos(tmp_path):
    with patch("review_issue.prompt.interactive", return_value=True), \
         patch("review_issue.prompt.ask", side_effect=["linear", "all"]):
        result = ensure_issue_provider(str(tmp_path))
    assert result.name == "linear"
    config_path = tmp_path / "workbench-config" / "config.yml"
    assert "provider: linear" in config_path.read_text()
    assert not (tmp_path / ".workbench.yml").exists()


def test_ensure_issue_provider_with_no_path_asks_once_and_writes_globally(tmp_path):
    """No repo to write to, so there is no scope question — just the provider one."""
    with patch("review_issue.prompt.interactive", return_value=True), \
         patch("review_issue.prompt.ask", return_value="linear") as asked:
        result = ensure_issue_provider()
    assert result.name == "linear"
    assert asked.call_count == 1
    config_path = tmp_path / "workbench-config" / "config.yml"
    assert "provider: linear" in config_path.read_text()


def test_ensure_issue_provider_does_not_record_a_declined_answer(tmp_path):
    """An empty answer must not write a value — that is how a guess gets in."""
    with patch("review_issue.prompt.interactive", return_value=True), \
         patch("review_issue.prompt.ask", return_value=""):
        result = ensure_issue_provider(str(tmp_path))
    assert result.resolved is False
    assert not (tmp_path / ".workbench.yml").exists()
    assert not (tmp_path / "workbench-config" / "config.yml").exists()


def test_ensure_issue_provider_rejects_an_unrecognised_answer(tmp_path, capsys):
    with patch("review_issue.prompt.interactive", return_value=True), \
         patch("review_issue.prompt.ask", return_value="bitbucket"):
        result = ensure_issue_provider(str(tmp_path))
    assert result.resolved is False
    assert "bitbucket" in capsys.readouterr().err
    assert not (tmp_path / ".workbench.yml").exists()


def test_ensure_issue_provider_rejects_an_unrecognised_scope(tmp_path, capsys):
    """A garbled scope answer must not silently pick a scope, repo or global."""
    with patch("review_issue.prompt.interactive", return_value=True), \
         patch("review_issue.prompt.ask", side_effect=["github", "global"]):
        result = ensure_issue_provider(str(tmp_path))
    assert result.name == "github"
    assert "global" in capsys.readouterr().err
    assert not (tmp_path / ".workbench.yml").exists()
    assert not (tmp_path / "workbench-config" / "config.yml").exists()


def test_ensure_issue_provider_still_resolves_when_the_write_fails(tmp_path, capsys):
    """A read-only checkout costs the recording, not the run."""
    with patch("review_issue.prompt.interactive", return_value=True), \
         patch("review_issue.prompt.ask", side_effect=["github", "repo"]), \
         patch(
             "review_issue.workbench_config.set_project_value",
             side_effect=workbench_config.ConfigError("read-only"),
         ):
        result = ensure_issue_provider(str(tmp_path))
    assert result.name == "github"
    assert "could not record the tracker" in capsys.readouterr().err


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


@pytest.fixture(autouse=True)
def _publishing_allowed(publishing_on):
    """These cover what a write does once it is allowed; the gate itself is
    covered in pr_comments_test.py."""


def test_create_issue_linear():
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


def test_create_issue_linear_no_parent():
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


def test_create_issue_github():
    r = MagicMock()
    r.returncode = 0
    r.stdout = "https://github.com/owner/repo/issues/42\n"

    with patch("subprocess.run", return_value=r):
        result = create_issue("github", "", "title", "description", repo="owner/repo")

    assert result is not None
    assert result.id == "#42"
    assert result.url == "https://github.com/owner/repo/issues/42"


def test_create_issue_unsupported_provider():
    result = create_issue("jira", "PROJ", "title", "description")
    assert result is None


# ── update_issue ──────────────────────────────────────────────────────────


def test_update_issue_linear():
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


def test_update_issue_github():
    r = MagicMock()
    r.returncode = 0
    r.stdout = ""

    with patch("subprocess.run", return_value=r):
        ok = update_issue("github", "#42", "new description", repo="owner/repo")

    assert ok is True


def test_update_issue_unsupported_provider():
    ok = update_issue("jira", "PROJ-42", "description")
    assert ok is False
