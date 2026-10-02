"""Tests for review.issue filing — create_issue, labels, and update_issue."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review.issue import (
    IssueDelivery,
    CreatedIssue,
    load_issue_provider,
    create_issue,
    update_issue,
)
import config.workbench_config


# ── create_issue ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _publishing_allowed(publishing_on):
    """These cover what a write does once it is allowed; the gate itself is
    covered in the pr_comments suites."""


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

    assert result.filed is True
    assert result.owed is False
    assert result.issue.id == "ENG-456"
    assert result.issue.url == "https://linear.app/team/issue/ENG-456/slug"
    create_cmd = _issue_create_cmd(calls)
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

    assert result.filed is True
    assert result.issue.id == "ENG-456"


def test_create_issue_linear_failure():
    r = MagicMock()
    r.returncode = 1
    r.stdout = ""

    with patch("subprocess.run", return_value=r):
        result = create_issue("linear", "ENG", "title", "description")

    assert result.delivery is IssueDelivery.UNDELIVERED
    assert result.owed is True
    assert result.issue == CreatedIssue()


def test_create_issue_github():
    r = MagicMock()
    r.returncode = 0
    r.stdout = "https://github.com/owner/repo/issues/42\n"

    with patch("subprocess.run", return_value=r):
        result = create_issue("github", "", "title", "description", repo="owner/repo")

    assert result.filed is True
    assert result.issue.id == "#42"
    assert result.issue.url == "https://github.com/owner/repo/issues/42"


def test_create_issue_github_files_on_the_enterprise_host():
    """A write on the wrong host files the issue on the wrong instance, so the
    create carries the host the same way the read does."""
    r = MagicMock()
    r.returncode = 0
    r.stdout = "https://ghe.example.com/owner/repo/issues/42\n"
    opts = {"base_url": "https://ghe.example.com"}

    with patch("subprocess.run", return_value=r) as mock_run:
        create_issue(
            "github", "", "title", "description", repo="owner/repo", opts=opts,
        )

    argv = mock_run.call_args[0][0]
    assert "ghe.example.com/owner/repo" in argv


def test_create_issue_github_labels_are_resolved_on_the_same_host():
    """The label lookup precedes the filing and must not read github.com's
    labels to decide what the enterprise repo can be filed against."""
    r = MagicMock()
    r.returncode = 0
    r.stdout = '[{"name":"follow-up"}]'
    opts = {"base_url": "https://ghe.example.com", "labels": ["follow-up"]}

    with patch("subprocess.run", return_value=r) as mock_run:
        create_issue(
            "github", "", "title", "description", repo="owner/repo", opts=opts,
        )

    label_calls = [
        c[0][0] for c in mock_run.call_args_list if "label" in c[0][0]
    ]
    assert label_calls, "expected a label lookup before filing"
    assert all("ghe.example.com/owner/repo" in argv for argv in label_calls)


def test_update_issue_github_edits_on_the_enterprise_host():
    """An edit against the default host rewrites a body on the wrong instance."""
    r = MagicMock()
    r.returncode = 0
    r.stdout = ""
    opts = {"base_url": "https://ghe.example.com"}

    with patch("subprocess.run", return_value=r) as mock_run:
        update_issue("github", "#42", "new body", repo="owner/repo", opts=opts)

    argv = mock_run.call_args[0][0]
    assert "ghe.example.com/owner/repo" in argv


def test_create_issue_unsupported_provider():
    """A provider that cannot create issues still owes the issue it did not file."""
    result = create_issue("jira", "PROJ", "title", "description")
    assert result.delivery is IssueDelivery.UNDELIVERED
    assert result.owed is True


# ── Labels ─────────────────────────────────────────────────────────

# The production default, so these exercise the label a filing really carries
# rather than one that happens to match it today.
LABEL = config.workbench_config.FOLLOW_UP_LABEL


def _linear_calls(existing_labels, create_label_ok=True):
    """Record every linear invocation, answering the label list with *existing*."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        if "label" in cmd and "list" in cmd:
            r.stdout = json.dumps([{"name": name} for name in existing_labels])
        elif "label" in cmd and "create" in cmd:
            r.returncode = 0 if create_label_ok else 1
            r.stdout = "Created label" if create_label_ok else ""
        elif "issue" in cmd and "create" in cmd:
            r.stdout = "Created ENG-456"
        else:
            r.stdout = '{"url": "https://linear.app/team/issue/ENG-456/slug"}'
        return r

    return calls, fake_run


def _issue_create_cmd(calls):
    """The one call that filed the issue, out of the label traffic around it."""
    create_cmd = next((c for c in calls if "issue" in c and "create" in c), None)
    assert create_cmd is not None, f"no issue was filed; calls were {calls}"
    return create_cmd


def test_create_issue_carries_the_configured_labels(tmp_path):
    """The labels a repo declares reach the tracker's create command."""
    calls, fake_run = _linear_calls([LABEL])

    with patch("subprocess.run", side_effect=fake_run):
        result = create_issue(
            "linear", "ENG", "title", "description",
            opts={"labels": [LABEL]},
        )

    assert result.filed is True
    create_cmd = _issue_create_cmd(calls)
    assert "--label" in create_cmd
    assert LABEL in create_cmd


def test_a_repo_that_declares_no_labels_files_an_unlabelled_issue():
    """`labels: []` is an opt-out, so nothing is applied and nothing is created."""
    calls, fake_run = _linear_calls([])

    with patch("subprocess.run", side_effect=fake_run):
        result = create_issue("linear", "ENG", "title", "description", opts={"labels": []})

    assert result.filed is True
    assert "--label" not in _issue_create_cmd(calls)
    assert not [c for c in calls if "label" in c]


def test_a_label_the_tracker_lacks_is_created_before_the_issue():
    """Both CLIs refuse to file against an unknown label, so the gap is filled first."""
    calls, fake_run = _linear_calls([])

    with patch("subprocess.run", side_effect=fake_run):
        create_issue(
            "linear", "ENG", "title", "description", opts={"labels": [LABEL]},
        )

    label_create = next(c for c in calls if "label" in c and "create" in c)
    assert "--name" in label_create
    assert LABEL in label_create
    assert "--team" in label_create
    assert calls.index(label_create) < calls.index(_issue_create_cmd(calls))


def test_a_label_the_tracker_already_holds_is_not_created_again():
    calls, fake_run = _linear_calls([LABEL])

    with patch("subprocess.run", side_effect=fake_run):
        create_issue(
            "linear", "ENG", "title", "description", opts={"labels": [LABEL]},
        )

    assert not [c for c in calls if "label" in c and "create" in c]


def test_a_label_matching_case_insensitively_is_not_created_again():
    """Both trackers resolve a label name case-insensitively, so `Follow-Up` is a hit."""
    calls, fake_run = _linear_calls([LABEL.title()])

    with patch("subprocess.run", side_effect=fake_run):
        create_issue(
            "linear", "ENG", "title", "description", opts={"labels": [LABEL]},
        )

    assert not [c for c in calls if "label" in c and "create" in c]


def test_a_label_that_cannot_be_created_is_dropped_rather_than_failing_the_issue():
    """An unlabelled tracking issue is worth more than no tracking issue."""
    calls, fake_run = _linear_calls([], create_label_ok=False)

    with patch("subprocess.run", side_effect=fake_run):
        result = create_issue(
            "linear", "ENG", "title", "description", opts={"labels": [LABEL]},
        )

    assert result.filed is True
    assert "--label" not in _issue_create_cmd(calls)


def test_a_label_created_without_output_is_still_created():
    """A mutation is judged by its exit code: silent success is not failure.

    `_run_issue_cli` reports a failure as empty stdout, so reading it for this
    would drop a label that had just been created and warn that it had not.
    """
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        if "label" in cmd and "list" in cmd:
            r.stdout = "[]"
        elif "label" in cmd and "create" in cmd:
            r.stdout = ""
        elif "issue" in cmd and "create" in cmd:
            r.stdout = "Created ENG-456"
        else:
            r.stdout = '{"url": "https://linear.app/team/issue/ENG-456/slug"}'
        return r

    with patch("subprocess.run", side_effect=fake_run):
        create_issue("linear", "ENG", "title", "description", opts={"labels": [LABEL]})

    assert LABEL in _issue_create_cmd(calls)


def test_github_labels_reach_the_create_command():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        if "label" in cmd and "list" in cmd:
            r.stdout = json.dumps([{"name": LABEL}])
        elif "issue" in cmd and "create" in cmd:
            r.stdout = "https://github.com/owner/repo/issues/42\n"
        return r

    with patch("subprocess.run", side_effect=fake_run):
        result = create_issue(
            "github", "", "title", "description", repo="owner/repo",
            opts={"labels": [LABEL]},
        )

    assert result.filed is True
    create_cmd = _issue_create_cmd(calls)
    assert "--label" in create_cmd
    assert LABEL in create_cmd


def test_a_github_issue_is_assigned_to_whoever_filed_it():
    """Linear's creator has always self-assigned; this one had not."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        r.stdout = "https://github.com/owner/repo/issues/42\n"
        return r

    with patch("subprocess.run", side_effect=fake_run):
        create_issue("github", "", "title", "description", repo="owner/repo")

    create_cmd = _issue_create_cmd(calls)
    assert "--assignee" in create_cmd
    assert "@me" in create_cmd


def test_the_configured_labels_reach_a_filing_from_the_repo_config(tmp_path):
    """The list survives the config round trip rather than arriving stringified."""
    (tmp_path / ".workbench.yml").write_text(
        f"issues:\n  provider: github\n  labels:\n    - {LABEL}\n",
    )
    info = load_issue_provider(str(tmp_path))
    assert info.options["labels"] == [LABEL]


def test_a_repo_config_opting_out_files_an_unlabelled_issue(tmp_path):
    """`labels: []` survives the whole path, not just the two halves of it.

    The options dict is built with a truthiness filter, so an explicit empty
    list is dropped exactly as a missing key is, and `_configured_labels`
    answers `[]` to both. That composition is what this covers: each half is
    tested alone, and either could stop meaning "no labels" without failing.
    """
    (tmp_path / ".workbench.yml").write_text(
        "issues:\n  provider: github\n  labels: []\n",
    )
    info = load_issue_provider(str(tmp_path))
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        r.stdout = "https://github.com/owner/repo/issues/42\n"
        return r

    with patch("subprocess.run", side_effect=fake_run):
        result = create_issue(
            info.name, "", "title", "description", repo="owner/repo",
            opts=info.options,
        )

    assert result.filed is True
    assert "--label" not in _issue_create_cmd(calls)
    assert not [c for c in calls if "label" in c]


def test_the_default_config_labels_a_filing_follow_up():
    """Every repo gets the label unless it says otherwise."""
    assert config.workbench_config.IssuesConfig().labels == [LABEL]


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
