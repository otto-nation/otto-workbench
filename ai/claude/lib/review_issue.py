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


def load_issue_provider(wt_path: str | None = None) -> IssueProvider:
    config_file = ""
    if wt_path and os.path.isfile(os.path.join(wt_path, ".claude", "review.yml")):
        config_file = os.path.join(wt_path, ".claude", "review.yml")
    else:
        workbench_dir = os.environ.get(
            "WORKBENCH_STATE_DIR", os.path.expanduser("~/.config/workbench")
        )
        candidate = os.path.join(workbench_dir, "review.yml")
        if os.path.isfile(candidate):
            config_file = candidate

    if not config_file:
        return IssueProvider()

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
            try:
                opts = json.loads(r_opts.stdout.strip())
            except json.JSONDecodeError:
                pass
    except (FileNotFoundError, subprocess.TimeoutExpired):
        with open(config_file) as f:
            for line in f:
                m = re.match(r"\s*provider:\s*(.+)", line)
                if m:
                    provider = m.group(1).strip().strip("'\"")
                    break

    if not provider:
        provider = _PROVIDER_DEFAULT

    return IssueProvider(name=provider, options=opts)


def extract_issue_id(provider: str, branch: str, pr_body: str = "") -> str | None:
    if provider in ("linear", "jira"):
        m = _ISSUE_PATTERN_JIRA_LINEAR.search(branch)
        if m:
            return m.group(0)
        if pr_body:
            m = _ISSUE_PATTERN_JIRA_LINEAR.search(pr_body)
            if m:
                return m.group(0)
        return None

    if provider == "github":
        if pr_body:
            m = _GITHUB_CLOSE_PATTERN.search(pr_body)
            if m:
                return m.group(2)
        return None

    return None


def fetch_issue_context(
    provider: str,
    issue_id: str | None,
    repo: str = "",
    opts: dict | None = None,
) -> IssueContext:
    if not issue_id:
        return IssueContext()

    if provider == "linear":
        try:
            r = subprocess.run(
                ["linear", "issue", "view", issue_id, "--json", "--no-comments"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0 and r.stdout.strip():
                print(f"✓ Found Linear issue: {issue_id}", file=sys.stderr)
                return IssueContext(context=r.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return IssueContext()

    if provider == "github":
        link = f"{_GITHUB_BASE_URL}/{repo}/issues/{issue_id}"
        try:
            r = subprocess.run(
                ["gh", "issue", "view", issue_id, "--repo", repo, "--json", "title,body,comments"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0 and r.stdout.strip():
                print(f"✓ Found GitHub issue: #{issue_id}", file=sys.stderr)
                return IssueContext(link=link, context=r.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return IssueContext(link=link)

    if provider == "jira":
        link = ""
        jira_url = (opts or {}).get("jira_url", "")
        if jira_url:
            link = f"{jira_url}/browse/{issue_id}"
        print(f"✓ Found Jira issue: {issue_id}", file=sys.stderr)
        return IssueContext(link=link)

    return IssueContext()
