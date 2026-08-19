"""Issue tracking integration for claude-review."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import log
import publishing
import workbench_config
from workbench_config import yaml_dump

_ISSUE_PATTERN_JIRA_LINEAR = re.compile(r"[A-Z]+-[0-9]+")
_GITHUB_CLOSE_PATTERN = re.compile(r"(closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)
_GITHUB_BASE_URL = "https://github.com"

_LEGACY_CONFIG_DIR = ".claude"
_LEGACY_CONFIG_FILE = "review.yml"


@dataclass(frozen=True)
class IssueProviderInfo:
    """The resolved tracker: which provider, plus its settings as strings.

    Named apart from ``workbench_config.IssueProvider``, the enum of provider
    names this carries in ``name`` — the two are in scope together here.
    """

    name: str = str(workbench_config.IssueProvider.LINEAR)
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CreatedIssue:
    id: str = ""
    url: str = ""


@dataclass(frozen=True)
class IssueContext:
    link: str = ""
    context: str = ""


def adopt_project_review_yml(wt_path: str) -> bool:
    """Carry a repo's ``.claude/review.yml`` into ``.workbench.yml``, once.

    The machine-wide migration cannot reach the project files in every repo the
    user reviews, so the conversion happens where the file is found. Returns
    True when it converted one.

    The old file is left in place: it is typically tracked in the consumer
    repo, and deleting a tracked file during an unrelated command is a
    surprise, not a recovery. The message names it as removable instead.
    Idempotent — the new file existing is what stops a second conversion.
    """
    legacy = Path(wt_path) / _LEGACY_CONFIG_DIR / _LEGACY_CONFIG_FILE
    target = workbench_config.project_config_path(wt_path)
    if not legacy.is_file() or target.exists():
        return False

    try:
        legacy_data = workbench_config.read_yaml(legacy)
    except workbench_config.ConfigError as exc:
        log.warn(f"{legacy} is unreadable ({exc}) — not converting it")
        return False

    tracker = legacy_data.get("issue_tracker")
    if not isinstance(tracker, dict):
        return False

    try:
        target.write_text(yaml_dump({"review": {"issue_tracker": tracker}}))
    except OSError as exc:
        # A read-only checkout still has the legacy file to fall back on, so a
        # failed conversion costs nothing but the conversion.
        log.dim(f"could not write {target} ({exc}) — leaving {legacy} in place")
        return False

    log.ok(f"Converted {legacy} to {target.name} — the old file can be removed")
    return True


def load_issue_provider(wt_path: str | None = None) -> IssueProviderInfo:
    """The issue tracker for this scope, project config first.

    A repo still holding the legacy ``.claude/review.yml`` is converted on
    the way through, so the answer comes from one place afterwards.
    """
    if wt_path:
        adopt_project_review_yml(wt_path)
    config = workbench_config.load_config_or_default(wt_path)
    tracker = config.review.issue_tracker
    # str() per value: asdict leaves an enum member as the member, and every
    # consumer of options reads it as a string. A None provider is dropped by
    # the same truthiness filter, so options never carries a "None" string.
    options = {k: str(v) for k, v in dataclasses.asdict(tracker).items() if v}
    name = str(tracker.provider) if tracker.provider is not None else ""
    return IssueProviderInfo(name=name, options=options)


def _search_jira_linear_id(branch: str, pr_body: str) -> str | None:
    """Search branch then PR body for a Jira/Linear issue ID."""
    m = _ISSUE_PATTERN_JIRA_LINEAR.search(branch)
    if m:
        return m.group(0)
    if pr_body:
        m = _ISSUE_PATTERN_JIRA_LINEAR.search(pr_body)
        if m:
            return m.group(0)
    return None


def extract_issue_id(provider: str, branch: str, pr_body: str = "") -> str | None:
    if provider in ("linear", "jira"):
        issue_id = _search_jira_linear_id(branch, pr_body)
        if issue_id:
            return issue_id
        searched = [f"branch '{branch}'"]
        if pr_body:
            searched.append("PR body")
        log.dim(f"No {provider} issue ID found in {' or '.join(searched)}")
        return None

    if provider == "github" and pr_body:
        m = _GITHUB_CLOSE_PATTERN.search(pr_body)
        if m:
            return m.group(2)
        log.dim("No closes/fixes/resolves keyword found in PR body")
        return None

    return None


def _run_issue_cli(cmd: list[str]) -> str:
    """Run a CLI command and return stripped stdout, or empty string on failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if r.returncode != 0:
        return ""
    return r.stdout.strip()


def _fetch_linear(issue_id: str) -> IssueContext:
    """Fetch a Linear issue by ID."""
    context = _run_issue_cli(
        ["linear", "issue", "view", issue_id, "--json", "--no-comments"],
    )
    if context:
        log.ok(f"Found Linear issue: {issue_id}")
    else:
        log.dim(f"Linear issue {issue_id} not found or linear CLI unavailable")
    return IssueContext(context=context)


def _fetch_github(issue_id: str, repo: str) -> IssueContext:
    """Fetch a GitHub issue by number and repo."""
    link = f"{_GITHUB_BASE_URL}/{repo}/issues/{issue_id}"
    context = _run_issue_cli(
        ["gh", "issue", "view", issue_id, "--repo", repo, "--json", "title,body,comments"],
    )
    if context:
        log.ok(f"Found GitHub issue: #{issue_id}")
    else:
        log.dim(f"GitHub issue #{issue_id} not found in {repo}")
    return IssueContext(link=link, context=context)


def _fetch_jira(issue_id: str, opts: dict | None) -> IssueContext:
    """Build a Jira issue context from opts."""
    jira_url = (opts or {}).get("jira_url", "")
    link = f"{jira_url}/browse/{issue_id}" if jira_url else ""
    log.ok(f"Found Jira issue: {issue_id}")
    return IssueContext(link=link)


def fetch_issue_context(
    provider: str,
    issue_id: str | None,
    repo: str = "",
    opts: dict | None = None,
) -> IssueContext:
    if not issue_id:
        return IssueContext()

    if provider == "linear":
        return _fetch_linear(issue_id)

    if provider == "github":
        return _fetch_github(issue_id, repo)

    if provider == "jira":
        return _fetch_jira(issue_id, opts)

    return IssueContext()


# ── Issue creation / update ────────────────────────────────────────────────


@contextlib.contextmanager
def _description_file(description: str):
    fd, path = tempfile.mkstemp(suffix=".md", prefix="issue-desc-")
    with os.fdopen(fd, "w") as f:
        f.write(description)
    try:
        yield path
    finally:
        os.unlink(path)


def _create_linear(
    team: str,
    title: str,
    description: str,
    parent_id: str | None = None,
) -> CreatedIssue | None:
    with _description_file(description) as desc_file:
        cmd = [
            "linear", "issue", "create",
            "--team", team,
            "--assignee", "self",
            "--title", title,
            "--description-file", desc_file,
            "--no-interactive",
        ]
        if parent_id:
            cmd.extend(["--parent", parent_id])
        output = _run_issue_cli(cmd)
        if not output:
            return None
        m = _ISSUE_PATTERN_JIRA_LINEAR.search(output)
        if not m:
            log.error(f"Could not parse issue ID from linear output: {output[:200]}")
            return None
        issue_id = m.group(0)
        url = _get_linear_issue_url(issue_id)
        log.ok(f"Created Linear issue: {issue_id}")
        return CreatedIssue(id=issue_id, url=url)


def _get_linear_issue_url(issue_id: str) -> str:
    """Fetch the URL for a Linear issue via JSON view."""
    raw = _run_issue_cli(["linear", "issue", "view", issue_id, "--json", "--no-comments"])
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        return data.get("url", "")
    except json.JSONDecodeError:
        return ""


def get_issue_url(provider: str, issue_id: str) -> str:
    """Fetch the URL for an issue from the given provider."""
    if provider == "linear":
        return _get_linear_issue_url(issue_id)
    return ""


def _run_issue_cmd(cmd_prefix: list[str], description: str) -> bool:
    with _description_file(description) as desc_file:
        try:
            result = subprocess.run(
                cmd_prefix + [desc_file],
                capture_output=True, text=True, timeout=30,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False


def _update_linear(issue_id: str, description: str) -> bool:
    ok = _run_issue_cmd(
        ["linear", "issue", "update", issue_id, "--description-file"],
        description,
    )
    if ok:
        log.ok(f"Updated Linear issue: {issue_id}")
    return ok


def _create_github(
    repo: str, title: str, description: str,
) -> CreatedIssue | None:
    with _description_file(description) as desc_file:
        output = _run_issue_cli([
            "gh", "issue", "create",
            "--repo", repo,
            "--title", title,
            "--body-file", desc_file,
        ])
        if not output:
            return None
        url = output.strip().splitlines()[-1].strip()
        m = re.search(r"/issues/(\d+)", url)
        issue_id = f"#{m.group(1)}" if m else url
        log.ok(f"Created GitHub issue: {issue_id}")
        return CreatedIssue(id=issue_id, url=url)


def _update_github(repo: str, issue_id: str, description: str) -> bool:
    num = issue_id.lstrip("#")
    ok = _run_issue_cmd(
        ["gh", "issue", "edit", num, "--repo", repo, "--body-file"],
        description,
    )
    if ok:
        log.ok(f"Updated GitHub issue: {issue_id}")
    return ok


def create_issue(
    provider: str,
    team: str,
    title: str,
    description: str,
    parent_id: str | None = None,
    repo: str = "",
    opts: dict | None = None,
) -> CreatedIssue | None:
    """Create an issue in the configured tracker. Returns None on failure."""
    if not publishing.enabled():
        publishing.draft(f"create {provider} issue: {title}", description)
        return None
    if provider == "linear":
        return _create_linear(team, title, description, parent_id)
    if provider == "github":
        return _create_github(repo, title, description)
    log.dim(f"Issue creation not supported for provider: {provider}")
    return None


def update_issue(
    provider: str,
    issue_id: str,
    description: str,
    repo: str = "",
    opts: dict | None = None,
) -> bool:
    """Update an existing issue's description. Returns True on success."""
    if not publishing.enabled():
        publishing.draft(f"update {provider} issue {issue_id}", description)
        return False
    if provider == "linear":
        return _update_linear(issue_id, description)
    if provider == "github":
        return _update_github(repo, issue_id, description)
    log.dim(f"Issue update not supported for provider: {provider}")
    return False
