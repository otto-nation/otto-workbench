"""Issue tracking integration for claude-review."""

# doc-group: publishing

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import gh_client
import log
import proc
import prompt
import publishing
import timeouts
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

    An empty ``name`` means no repo and no machine has said where issues go.
    Read ``resolved`` rather than testing the string, so a call site states
    what it is asking.
    """

    name: str = ""
    options: dict = field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        return bool(self.name)


@dataclass(frozen=True)
class CreatedIssue:
    id: str = ""
    url: str = ""


class IssueDelivery(StrEnum):
    """What became of an issue write, for a caller that has to report it.

    ``create_issue`` used to answer ``None`` both when the publishing gate
    declined the write and when the tracker refused it, so every caller read a
    draft run as a failure. Naming the two apart is what lets a call site say
    which one happened without asking ``publishing.enabled()`` a second time.
    """

    FILED = "filed"
    # Nothing was attempted and nothing is owed: the publishing gate declined
    # the write, or the caller had nothing to address it to while the gate was
    # shut. A draft run belongs here — the gate doing its job is not a failure.
    SKIPPED = "skipped"
    # The write was owed and did not land: the tracker refused it, the provider
    # cannot create issues, or no tracker is configured. Whoever was counting on
    # the issue is still owed one.
    UNDELIVERED = "undelivered"


@dataclass(frozen=True)
class IssueResult:
    """An issue write: what it produced, and whether anything is still owed."""

    delivery: IssueDelivery
    issue: CreatedIssue = field(default_factory=CreatedIssue)

    @property
    def filed(self) -> bool:
        return self.delivery is IssueDelivery.FILED

    @property
    def owed(self) -> bool:
        return self.delivery is IssueDelivery.UNDELIVERED


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

    body = yaml_dump({"issue_tracker": tracker})
    try:
        # The modeline every other creator seeds, so a converted file gets the
        # same schema completion a file `set_value` created would.
        target.write_text(f"{workbench_config.CONFIG_HEADER}\n{body}")
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
    tracker = config.issue_tracker
    # str() per value: asdict leaves an enum member as the member, and every
    # consumer of options reads it as a string. A None provider is dropped by
    # the same truthiness filter, so options never carries a "None" string.
    options = {k: str(v) for k, v in dataclasses.asdict(tracker).items() if v}
    name = str(tracker.provider) if tracker.provider is not None else ""
    return IssueProviderInfo(name=name, options=options)


# Providers whose issue creation needs a team or project key. GitHub is
# addressed by repo, so requiring one there rejects a creation that would
# have worked. Jira is absent for the opposite reason: create_issue has no
# Jira branch, so gating it on a team key blames a missing key for a
# creation that a key would not have enabled.
_TEAM_KEY_PROVIDERS = frozenset({
    str(workbench_config.IssueProvider.LINEAR),
})


class _Scope(StrEnum):
    """How widely a recorded answer applies."""

    REPO = "repo"
    ALL = "all"


def needs_team_key(provider: str) -> bool:
    """Whether creating an issue with this provider requires a team key."""
    return provider in _TEAM_KEY_PROVIDERS


def _config_problem(wt_path: str | None) -> str:
    """Why this scope's config cannot be read, or ``""`` when it reads.

    ``load_issue_provider`` goes through ``load_config_or_default``, which
    reports a file it cannot parse as an unset provider. That is right for a
    hook and wrong for a caller about to offer to record the very key the
    broken file may already hold.
    """
    try:
        workbench_config.load_config(wt_path)
    except workbench_config.ConfigError as exc:
        return str(exc)
    return ""


def ensure_issue_provider(wt_path: str | None = None) -> IssueProviderInfo:
    """The issue tracker for this scope, asking for it when nothing has said.

    For callers that are about to file. ``load_issue_provider`` is the one to
    use when a missing tracker is survivable — enriching a review with issue
    context needs no tracker and must not stop to ask for one.

    An unanswered, declined, or unrecognised question leaves the provider
    unresolved. Nothing is written in those cases: a prompt that records
    something on a non-answer is another way to arrive at a guess, which is
    what this function exists to remove.
    """
    info = load_issue_provider(wt_path)
    if info.resolved:
        return info

    where = wt_path or "this repo"
    problem = _config_problem(wt_path)
    if problem:
        log.error(
            f"Cannot read the issue tracker for {where}: {problem} — fix the "
            f"file; an answer given now would be shadowed by it",
        )
        return info

    accepted = [str(p) for p in workbench_config.IssueProvider]
    if not prompt.interactive():
        log.warn(
            f"No issue tracker configured for {where} — set "
            f"{workbench_config.ISSUE_PROVIDER_KEY} to one of "
            f"{', '.join(accepted)} in {workbench_config.PROJECT_CONFIG_NAME} "
            f"or {workbench_config.global_config_path()}",
        )
        return info

    answer = prompt.ask(
        f"Where does {where} file issues? ({'/'.join(accepted)}, Enter to skip): ",
    ).lower()
    if not answer:
        log.warn(
            f"No issue tracker recorded for {where} — nothing was filed",
        )
        return info
    if answer not in accepted:
        log.warn(f"'{answer}' is not one of {', '.join(accepted)} — nothing was recorded")
        return info

    _record_issue_provider(answer, wt_path)
    return IssueProviderInfo(name=answer, options={**info.options, "provider": answer})


def _record_issue_provider(provider: str, wt_path: str | None) -> None:
    """Persist the answer at the scope the user picks.

    A failed write, or an unrecognised scope answer, is reported and
    swallowed: the caller has an answer for this run either way, and a
    read-only checkout — or a garbled scope — should cost the recording
    rather than the filing. ``adopt_project_review_yml`` makes the same
    trade on a failed write.
    """
    if wt_path is None:
        scope = str(_Scope.ALL)
    else:
        scope = prompt.ask(
            f"Record for this repo or all repos? "
            f"({_Scope.REPO}/{_Scope.ALL}, Enter for {_Scope.REPO}): ",
        ).lower()

    try:
        chosen = _Scope(scope or _Scope.REPO)
    except ValueError:
        log.warn(f"'{scope}' is not {_Scope.REPO} or {_Scope.ALL} — nothing was recorded")
        return
    scope_all = chosen is _Scope.ALL

    try:
        if scope_all:
            workbench_config.set_value(workbench_config.ISSUE_PROVIDER_KEY, provider)
            log.ok(f"Recorded {provider} as the tracker for all repos")
            return
        workbench_config.set_project_value(
            workbench_config.ISSUE_PROVIDER_KEY, provider, wt_path,
        )
        log.ok(
            f"Recorded {provider} in {workbench_config.PROJECT_CONFIG_NAME} "
            f"— commit it so the repo keeps the answer",
        )
    except workbench_config.ConfigError as exc:
        log.dim(f"could not record the tracker ({exc}) — using {provider} for this run")


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
    """Run a CLI command and return stripped stdout, or empty string on failure.

    A timeout arrives as a failed result rather than an exception, so only the
    missing-binary case still needs catching — the tracker CLI is optional.
    """
    try:
        r = proc.run(cmd, timeout=timeouts.NETWORK)
    except FileNotFoundError:
        return ""
    return r.stdout.strip() if r.ok else ""


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
    context = gh_client.out(
        "issue", "view", issue_id, "--repo", repo, "--json", "title,body,comments",
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
            return proc.run(cmd_prefix + [desc_file], timeout=timeouts.NETWORK).ok
        except FileNotFoundError:
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
        output = gh_client.out(
            "issue", "create",
            "--repo", repo,
            "--title", title,
            "--body-file", desc_file,
        )
        if not output:
            return None
        url = output.strip().splitlines()[-1].strip()
        m = re.search(r"/issues/(\d+)", url)
        issue_id = f"#{m.group(1)}" if m else url
        log.ok(f"Created GitHub issue: {issue_id}")
        return CreatedIssue(id=issue_id, url=url)


def _update_github(repo: str, issue_id: str, description: str) -> bool:
    num = issue_id.lstrip("#")
    with _description_file(description) as desc_file:
        ok = gh_client.ok("issue", "edit", num, "--repo", repo, "--body-file", desc_file)
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
) -> IssueResult:
    """Create an issue in the configured tracker.

    Reports what happened rather than only what it produced: a draft run is
    ``SKIPPED``, because the publishing gate declining a write is the gate
    working and leaves nothing owed, while a tracker that refused the write —
    or a provider that cannot create issues at all — is ``UNDELIVERED``.
    """
    if not publishing.enabled():
        publishing.draft(f"create {provider} issue: {title}", description)
        return IssueResult(IssueDelivery.SKIPPED)
    if provider == "linear":
        return _creation_result(_create_linear(team, title, description, parent_id))
    if provider == "github":
        return _creation_result(_create_github(repo, title, description))
    log.dim(f"Issue creation not supported for provider: {provider}")
    return IssueResult(IssueDelivery.UNDELIVERED)


def _creation_result(issue: CreatedIssue | None) -> IssueResult:
    """Read a per-provider creator's answer as a delivery."""
    if issue is None:
        return IssueResult(IssueDelivery.UNDELIVERED)
    return IssueResult(IssueDelivery.FILED, issue)


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
