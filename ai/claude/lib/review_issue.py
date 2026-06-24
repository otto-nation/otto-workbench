"""Issue tracking integration for claude-review."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

_ISSUE_PATTERN_JIRA_LINEAR = re.compile(r"[A-Z]+-[0-9]+")
_GITHUB_CLOSE_PATTERN = re.compile(r"(closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)
_GITHUB_BASE_URL = "https://github.com"
_PROVIDER_DEFAULT = "linear"


@dataclass(frozen=True)
class IssueProvider:
    name: str = _PROVIDER_DEFAULT
    options: dict = field(default_factory=dict)


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
    if wt_path and os.path.isfile(os.path.join(wt_path, ".claude", "review.yml")):
        return os.path.join(wt_path, ".claude", "review.yml")

    workbench_dir = os.environ.get(
        "WORKBENCH_STATE_DIR", os.path.expanduser("~/.config/workbench")
    )
    candidate = os.path.join(workbench_dir, "review.yml")
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
        return _search_jira_linear_id(branch, pr_body)

    if provider == "github" and pr_body:
        m = _GITHUB_CLOSE_PATTERN.search(pr_body)
        return m.group(2) if m else None

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
        print(f"✓ Found Linear issue: {issue_id}", file=sys.stderr)
    return IssueContext(context=context)


def _fetch_github(issue_id: str, repo: str) -> IssueContext:
    """Fetch a GitHub issue by number and repo."""
    link = f"{_GITHUB_BASE_URL}/{repo}/issues/{issue_id}"
    context = _run_issue_cli(
        ["gh", "issue", "view", issue_id, "--repo", repo, "--json", "title,body,comments"],
    )
    if context:
        print(f"✓ Found GitHub issue: #{issue_id}", file=sys.stderr)
    return IssueContext(link=link, context=context)


def _fetch_jira(issue_id: str, opts: dict | None) -> IssueContext:
    """Build a Jira issue context from opts."""
    jira_url = (opts or {}).get("jira_url", "")
    link = f"{jira_url}/browse/{issue_id}" if jira_url else ""
    print(f"✓ Found Jira issue: {issue_id}", file=sys.stderr)
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
