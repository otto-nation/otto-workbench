"""Generate a new PR's title and body.

Resolves the repo template, gathers branch facts, and either fills from
overrides, the single commit, or an AI call on ``Phase.CREATE``. Marker
extraction is the only parse of the model answer — nothing outside
``<<<TITLE>>>`` / ``<<<DESCRIPTION>>>`` is kept.
"""

# doc-group: publishing

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import agent.invoke
import core.pr_template
import git.client
from core.phases import Phase
from core.pr_template import PRTemplate
from pr.close_refs import append

# `git_remote` is a workbench-wide module rather than an `ai/lib` one, because
# the pre-push hooks and the surface gate resolve the same default branch. In a
# checkout that is one directory up; in the otto-ai-tools tarball, which
# flattens both into one `lib/`, it is one directory up from this file too —
# not beside it — and the path below does not exist.
_WORKBENCH_LIB = Path(__file__).resolve().parent.parent.parent.parent / "lib"
if _WORKBENCH_LIB.is_dir() and str(_WORKBENCH_LIB) not in sys.path:
    sys.path.insert(0, str(_WORKBENCH_LIB))
import git_remote  # noqa: E402

_ISSUE = re.compile(r"[A-Z]+-[0-9]+")
_GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


class ContentError(Exception):
    """A user-facing ✗ message. The string is what the caller prints."""


@dataclass(frozen=True)
class BranchFacts:
    """What git says about the commits ahead of ``origin/<base>``."""

    commits: str
    count: int
    files: str
    head_subject: str
    head_body: str


@dataclass(frozen=True)
class ContentRequest:
    """Inputs ``generate`` needs from the CLI, already normalised."""

    branch: str
    base: str
    issue: str
    title: str
    body: str
    closes: tuple[str, ...]


@dataclass(frozen=True)
class PRContent:
    """Title, body, and what ``close_refs.append`` did to the body."""

    title: str
    body: str
    linked: tuple[str, ...]
    already: tuple[str, ...]


def extract(text: str, name: str) -> str | None:
    """Content between ``<<<NAME>>>`` and ``<<<END_NAME>>>`` lines, stripped.

    ``None`` if either marker is absent, the end precedes the start, or the
    span is empty.
    """
    begin = f"<<<{name}>>>"
    end = f"<<<END_{name}>>>"
    lines = text.splitlines()
    try:
        start = lines.index(begin)
        stop = lines.index(end)
    except ValueError:
        return None
    if stop <= start:
        return None
    content = "\n".join(lines[start + 1:stop]).strip()
    return content or None


def resolve_issue(branch: str, override: str) -> str:
    """``override`` if set, else the first ``[A-Z]+-[0-9]+`` in ``branch``."""
    if override:
        return override
    match = _ISSUE.search(branch)
    return match.group(0) if match else ""


def missing_sections(template: PRTemplate, body: str) -> list[str]:
    """Headers the body still owes. Empty when the repo ships no template."""
    if not template.found:
        return []
    return [header for header in template.headers if header not in body]


def template_refusal(template: PRTemplate, missing: list[str]) -> str:
    """The three-line bash refusal, as one string."""
    listed = "\n".join(f"  {header}" for header in missing)
    return (
        "✗ The PR body does not use this repo's template\n"
        f"→ Missing section(s) from {template.path}:\n"
        f"{listed}\n"
        "→ Use the template's own headers, or drop --body to have them filled"
    )


def branch_facts(wt: Path, base: str) -> BranchFacts:
    """Read the commits, files, and HEAD message. Git failure raises ContentError."""
    remote_base = f"{git_remote.GIT_REMOTE}/{base}"
    commits = _git(wt, "log", "--oneline", f"{remote_base}..HEAD")
    count_text = _git(wt, "rev-list", "--count", f"{remote_base}..HEAD")
    files = _git(wt, "diff", "--name-only", f"{remote_base}...HEAD")
    head_subject = _git(wt, "log", "-1", "--format=%s")
    head_body = _git(wt, "log", "-1", "--format=%b")
    try:
        count = int(count_text)
    except ValueError as exc:
        raise ContentError(
            f"✗ git rev-list --count {remote_base}..HEAD failed"
        ) from exc
    return BranchFacts(
        commits=commits,
        count=count,
        files=files,
        head_subject=head_subject,
        head_body=head_body,
    )


def generate(
    wt: Path, req: ContentRequest, *, repo: str, log: Callable[[str], None],
) -> PRContent:
    """Build the title and body, raising ContentError on every ✗."""
    template = _load_template(wt)

    if req.title and req.body:
        body = _checked_body(template, req.body)
        return _finish(req.title, body, req.closes)

    facts = branch_facts(wt, req.base)
    if facts.count == 0:
        raise ContentError(
            f"✗ No commits on {req.branch} ahead of "
            f"{git_remote.GIT_REMOTE}/{req.base} — nothing to open a PR for"
        )

    issue = resolve_issue(req.branch, req.issue)
    if issue:
        if req.issue:
            log(f"✓ Using issue: {issue}")
        else:
            log(f"✓ Found issue number: {issue}")

    if facts.count == 1:
        title, body = _single(wt, template, facts, repo, log)
    else:
        title, body = _multi(wt, req, template, facts, issue, repo)

    if req.title:
        title = req.title
    if req.body:
        body = _checked_body(template, req.body)
    return _finish(title, body, req.closes)


def _git_env() -> dict[str, str]:
    """The process environment with inherited git overrides dropped.

    ``GIT_DIR`` is read ahead of directory discovery, so a run under a hook
    would have ``--show-toplevel`` answer the cwd rather than the repo root.
    """
    env = os.environ.copy()
    for name in _GIT_ENV_DROP:
        env.pop(name, None)
    return env


def _git(wt: Path, *args: str) -> str:
    result = git.client.run(*args, cwd=wt, env=_git_env())
    if not result.ok:
        raise ContentError(f"✗ git {' '.join(args)} failed")
    return result.stdout.strip()


def _load_template(wt: Path) -> PRTemplate:
    try:
        root = Path(_git(wt, "rev-parse", "--show-toplevel"))
        return core.pr_template.load(root)
    except ContentError:
        raise ContentError("✗ Could not resolve this repo's PR template") from None
    except Exception as exc:
        raise ContentError("✗ Could not resolve this repo's PR template") from exc


def _checked_body(template: PRTemplate, body: str) -> str:
    missing = missing_sections(template, body)
    if missing:
        raise ContentError(template_refusal(template, missing))
    return body


def _finish(title: str, body: str, closes: tuple[str, ...]) -> PRContent:
    linked = append(body, closes)
    return PRContent(
        title=title, body=linked.body, linked=linked.linked, already=linked.already,
    )


def _usable_description(text: str) -> bool:
    return extract(text, "DESCRIPTION") is not None


def _usable_title_and_description(text: str) -> bool:
    return (
        extract(text, "TITLE") is not None
        and extract(text, "DESCRIPTION") is not None
    )


def _ask(
    wt: Path, prompt: str, usable: Callable[[str], bool], repo: str,
) -> str:
    result = agent.invoke.run_prompt(
        Phase.CREATE, prompt,
        cwd=wt, usable=usable, task="pr-create", repo=repo, pr=None,
    )
    if not result.ok:
        raise ContentError("✗ Could not generate the PR title and description")
    return result.text


def _single(
    wt: Path, template: PRTemplate, facts: BranchFacts, repo: str,
    log: Callable[[str], None],
) -> tuple[str, str]:
    title = facts.head_subject
    if template.found:
        log("→ Single commit — using commit message as title, AI filling template")
        prompt = (
            "Fill out this PR template based on the commit below.\n"
            "\n"
            "Template:\n"
            f"{template.text}\n"
            "\n"
            f"Commit subject: {facts.head_subject}\n"
            f"Commit body: {facts.head_body or '<none>'}\n"
            "\n"
            "Changed files:\n"
            f"{facts.files}\n"
            "\n"
            "Return the filled template between <<<DESCRIPTION>>> and "
            "<<<END_DESCRIPTION>>> on their own lines — no title, no commentary "
            "outside the markers."
        )
        text = _ask(wt, prompt, _usable_description, repo)
        body = extract(text, "DESCRIPTION")
        if body is None:
            raise ContentError("✗ Could not generate the PR title and description")
        return title, body

    log("→ Single commit — skipping AI, using commit message directly")
    if facts.head_body:
        return title, facts.head_body
    bullets = "\n".join(f"- {name}" for name in facts.files.splitlines() if name)
    return title, f"## Summary\n\n## Changes\n\n{bullets}\n\n## Testing"


def _multi(
    wt: Path, req: ContentRequest, template: PRTemplate, facts: BranchFacts,
    issue: str, repo: str,
) -> tuple[str, str]:
    prompt = (
        "Write a pull request title and fill out this template based on the "
        "changes.\n"
        "\n"
        "The title follows conventional commits: type(scope): subject, at most "
        "72 characters, no trailing period.\n"
        "\n"
        "Template:\n"
        f"{template.text}\n"
        "\n"
        f"Branch: {req.branch}\n"
        f"Issue: {issue or 'None'}\n"
        f"Commits: {facts.count}\n"
        "\n"
        "Recent commits:\n"
        f"{facts.commits}\n"
        "\n"
        "Changed files:\n"
        f"{facts.files}\n"
        "\n"
        "Return exactly:\n"
        "<<<TITLE>>>\n"
        "<title>\n"
        "<<<END_TITLE>>>\n"
        "<<<DESCRIPTION>>>\n"
        "<filled template>\n"
        "<<<END_DESCRIPTION>>>"
    )
    text = _ask(wt, prompt, _usable_title_and_description, repo)
    title = extract(text, "TITLE")
    body = extract(text, "DESCRIPTION")
    if title is None or body is None:
        raise ContentError("✗ Could not generate the PR title and description")
    return title, body
