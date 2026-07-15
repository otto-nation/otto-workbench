"""Issue tracking integration for claude-review."""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

import log
import review_common

_ISSUE_PATTERN_JIRA_LINEAR = re.compile(r"[A-Z]+-[0-9]+")
_GITHUB_CLOSE_PATTERN = re.compile(r"(closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)
_GITHUB_BASE_URL = "https://github.com"
_PROVIDER_DEFAULT = "linear"

_CONFIG_DIR = ".claude"
_CONFIG_FILE = "review.yml"


@dataclass(frozen=True)
class IssueProvider:
    name: str = _PROVIDER_DEFAULT
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CreatedIssue:
    id: str = ""
    url: str = ""


@dataclass(frozen=True)
class IssueContext:
    link: str = ""
    context: str = ""


def _parse_opts_json(raw: str) -> dict:
    """Parse JSON opts string, returning empty dict on failure."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _parse_provider_from_file(config_file: str) -> str:
    """Fallback: extract provider from YAML config by line scanning."""
    with open(config_file) as f:
        lines = f.readlines()
    for line in lines:
        m = re.match(r"\s*provider:\s*(.+)", line)
        if m:
            return m.group(1).strip().strip("'\"")
    return _PROVIDER_DEFAULT


def load_issue_provider(wt_path: str | None = None) -> IssueProvider:
    config_file = _find_config_file(wt_path)
    if not config_file:
        return IssueProvider()

    provider, opts = _load_provider_via_yq(config_file)

    if not provider:
        provider = _PROVIDER_DEFAULT

    return IssueProvider(name=provider, options=opts)


def _find_config_file(wt_path: str | None) -> str:
    """Locate the review.yml config file, or return empty string."""
    if wt_path:
        project_cfg = os.path.join(wt_path, _CONFIG_DIR, _CONFIG_FILE)
        if os.path.isfile(project_cfg):
            return project_cfg

    candidate = str(review_common.workbench_dir() / _CONFIG_FILE)
    if os.path.isfile(candidate):
        return candidate

    return ""


def _load_provider_via_yq(config_file: str) -> tuple[str, dict]:
    """Load provider and opts via yq, falling back to line scanning."""
    provider = _PROVIDER_DEFAULT
    opts: dict = {}

    try:
        r_provider = subprocess.run(
            ["yq", "-r", ".issue_tracker.provider // \"linear\"", config_file],
            capture_output=True, text=True, timeout=5,
        )
        if r_provider.returncode == 0 and r_provider.stdout.strip():
            provider = r_provider.stdout.strip()

        r_opts = subprocess.run(
            ["yq", "-r", ".issue_tracker // {}", config_file],
            capture_output=True, text=True, timeout=5,
        )
        if r_opts.returncode == 0 and r_opts.stdout.strip():
            opts = _parse_opts_json(r_opts.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        provider = _parse_provider_from_file(config_file)

    return provider, opts


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


def _update_linear(issue_id: str, description: str) -> bool:
    with _description_file(description) as desc_file:
        try:
            result = subprocess.run(
                ["linear", "issue", "update", issue_id, "--description-file", desc_file],
                capture_output=True, text=True, timeout=30,
            )
            ok = result.returncode == 0
            if ok:
                log.ok(f"Updated Linear issue: {issue_id}")
            return ok
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False


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
    with _description_file(description) as desc_file:
        try:
            num = issue_id.lstrip("#")
            result = subprocess.run(
                ["gh", "issue", "edit", num,
                 "--repo", repo, "--body-file", desc_file],
                capture_output=True, text=True, timeout=30,
            )
            ok = result.returncode == 0
            if ok:
                log.ok(f"Updated GitHub issue: {issue_id}")
            return ok
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False


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
    if provider == "linear":
        return _update_linear(issue_id, description)
    if provider == "github":
        return _update_github(repo, issue_id, description)
    log.dim(f"Issue update not supported for provider: {provider}")
    return False
