"""The describe pass: revise a PR's description against the repo's PR template.

Reads the PR body and the branch's commits, asks the agent for a revision in
the template's shape, validates what comes back, and applies or drafts it
under the publishing gate. Commit-aware: the HEAD it described is recorded, so
a repeated run against an unchanged branch is a no-op. Also projects the
branch's filed follow-up issues into the body.

`cli.pr_describe` is the command over this — arguments, target resolution, the
run lock and the trail. Template resolution is `core.pr_template`.
"""

# doc-group: pr-state

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import agent.invoke
import gh.client
import git.client
import git.topology
import core.log
import core.pr_template
import core.publishing
import core.run_lock
import pr.context
import pr.follow_ups
import pr.state
from core.phases import Phase
from pr.domains import DescribeSummary
from core.trail import Trail


# The binary a user runs and the trail records, which is not this module's own
# name. Spelled out rather than derived, so the shim can be renamed only by
# changing the name in both places at once.
SCRIPT = "pr-describe"

# The model says this, alone, when the body already conforms. Anything else is
# taken as the replacement body.
_NO_CHANGE = "DESCRIPTION_CURRENT"

# Extraction markers: the model wraps the revised description in these so we can
# parse and validate the response before posting it to GitHub.
_DESCRIBE_BEGIN = "<<<DESCRIPTION>>>"
_DESCRIBE_END = "<<<END_DESCRIPTION>>>"


def _git(cwd: Path, *args: str) -> str:
    r = git.client.run(*args, cwd=cwd)
    if not r.ok:
        # Silent fallback is intentional — the prompt degrades gracefully to
        # "(none)" for commits/changed files rather than aborting the pass.
        # Log so a bad revision (e.g. unfetched origin/<base>) is diagnosable.
        core.log.warn(f"git {' '.join(args)} failed: {r.detail}")
        return ""
    return r.stdout.strip()


def _fetch_pr_body(repo: str, pr_number: int) -> tuple[str, str] | None:
    """Return (title, body) for the PR, or None when gh cannot answer."""
    r = gh.client.run(
        "pr", "view", str(pr_number), "--repo", repo, "--json", "title,body",
    )
    if not r.ok:
        core.log.error(f"gh pr view failed: {r.detail}")
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        core.log.error("gh pr view returned unparseable JSON")
        return None
    return data.get("title", ""), data.get("body", "") or ""


def _build_prompt(
    template: str, has_template: bool, title: str, body: str,
    commits: str, changed_files: str,
) -> str:
    """Build the revision prompt.

    The template's own section headers are the contract — a repo that ships one
    expects exactly those sections, so the prompt forbids inventing others.
    """
    origin = (
        "the repo's checked-in PR template"
        if has_template else
        "the default template (this repo ships none)"
    )
    return f"""You are revising the description of an open pull request so it matches \
{origin} and the work the branch actually contains.

PR template — its section headers are required, and you must not add, rename, or
drop any of them:
{template}

Current PR title:
{title}

Current PR description:
{body or "(empty)"}

Commits on this branch (newest first):
{commits or "(none)"}

Files changed:
{changed_files or "(none)"}

Rules:
- Describe what the branch does now, not the order it was built in. Fix passes and
  review follow-ups are part of the change, not a changelog to recite.
- Keep any content in the current description that is still accurate.
- Reproduce any issue-closing line (Closes #12, Fixes ENG-34, Resolves #56) exactly
  as it appears. Dropping one un-links an issue that was linked on purpose.
- Do not mention AI assistance, agents, or tooling that produced the change.
- Do not append a footer.

If the current description already satisfies the template and describes the
branch accurately, reply with exactly {_NO_CHANGE} and nothing else.

Otherwise wrap the complete revised description in {_DESCRIBE_BEGIN} and \
{_DESCRIBE_END} markers — no preamble, no fencing, nothing outside the markers:
{_DESCRIBE_BEGIN}
(your revised description here)
{_DESCRIBE_END}"""


def _extract_description(text: str) -> str | None:
    """Extract the revised description from between the extraction markers.

    Returns None when either marker is missing or the content is blank —
    the caller must not post an unparseable response to GitHub.
    """
    begin = text.find(_DESCRIBE_BEGIN)
    end = text.find(_DESCRIBE_END)
    if begin == -1 or end == -1 or end <= begin:
        return None
    return text[begin + len(_DESCRIBE_BEGIN):end].strip() or None


def _usable_revision(text: str) -> bool:
    """The response must be the no-change sentinel or contain parseable markers."""
    t = text.strip()
    if t == _NO_CHANGE:
        return True
    return _extract_description(t) is not None


def _apply_body(repo: str, pr_number: int, body: str) -> bool:
    """Replace the PR body, when publishing is on. Returns whether it landed.

    Gated here rather than at the caller, matching `pr.comments._gh_post`: the
    policy belongs beside the write, so a second caller cannot reach GitHub by
    forgetting to ask. A draft reports False because nothing was written, and
    `run_describe` reads that as the body not having been applied.

    This is the write the gate most exists for — the text is AI-authored and
    replaces a description a human wrote.
    """
    if not core.publishing.enabled():
        core.publishing.draft(f"pr edit {repo}#{pr_number} --body-file -", body)
        return False
    r = gh.client.run(
        "pr", "edit", str(pr_number), "--repo", repo, "--body-file", "-",
        input_text=body,
    )
    if not r.ok:
        core.log.error(f"gh pr edit failed: {r.detail}")
        return False
    return True


def _persist(wt: Path, ctx: pr.context.ResolvedContext,
             summary: DescribeSummary) -> None:
    state = pr.state.load_or_init(
        target_dir=ctx.target_dir, repo=ctx.repo, branch=ctx.branch,
        pr_number=ctx.pr_number, head_sha=ctx.head_sha,
        worktree_root=str(wt),
    )
    pr.state.apply(state, summary)
    pr.state.save_state(ctx.target_dir, state)


@dataclasses.dataclass(frozen=True)
class Projection:
    """What the follow-up projection did, and what it read while doing it.

    A type rather than a tuple: two pieces of business data, and the caller
    reads `result.body` instead of learning which element of a pair is which.

    `title`/`body` are what the PR holds *after* this pass — the revision
    prompt that runs next needs the current body, and reusing this read is what
    keeps the projection to one `gh pr view` rather than two.
    """

    moved: bool = False
    title: str = ""
    body: str = ""
    # False when the body could not be read at all, which is different from
    # reading an empty one.
    fetched: bool = False


def project_follow_ups(
    ctx: pr.context.ResolvedContext, state, *, trail: Trail | None = None,
) -> Projection:
    """Put the branch's follow-ups in the PR body.

    Its own pass, ahead of the HEAD gate below, because a follow-up is filed
    without a commit far more often than with one: `pr comments --finish` writes
    state and posts replies and commits nothing at all. Behind the gate the
    entry would never reach the body, and the reviewer would never see it.

    Cheap enough to run unconditionally: one `gh pr view` and, only when the
    rendered block differs from what is already there, one `gh pr edit`. No AI
    call, which is what the HEAD gate exists to protect.
    """
    entries = state.follow_ups.entries if state else []
    if not entries:
        return Projection()

    fetched = _fetch_pr_body(ctx.repo, ctx.pr_number)
    if fetched is None:
        core.log.warn("could not read the PR body — leaving the follow-ups unprojected")
        return Projection()
    title, body = fetched

    projected = pr.follow_ups.project(body, entries)
    if projected.strip() == body.strip():
        # Already there. Still mark the entries, because an earlier run may have
        # written the block and failed before recording that it had.
        return Projection(_mark_projected(state, entries), title, body, True)

    if not _apply_body(ctx.repo, ctx.pr_number, projected):
        # Draft mode, or a write that failed. Either way the body does not hold
        # them, so the flags stay off and readiness keeps saying so.
        return Projection(False, title, body, True)

    core.log.info(f"Projected {len(entries)} follow-up(s) into the PR description")
    if trail:
        trail.info("describe", f"projected {len(entries)} follow-up(s)",
                   data={"pr": ctx.pr_number})
    return Projection(_mark_projected(state, entries), title, projected, True)


def _mark_projected(state, entries) -> bool:
    """Record that these entries have reached the body. Returns whether any moved.

    Filtered to `e.ref.id` first, matching `pr.follow_ups.render_block`'s own
    filter — an id-less entry is never written into the rendered block, so
    marking it projected here would tell `readiness()` a reviewer can see an
    entry that in fact never reached the body.
    """
    unmarked = [e for e in entries if e.ref.id and not e.in_pr_body]
    if not unmarked:
        return False
    pr.state.apply(state, pr.follow_ups.FollowUpDomain(
        entries=[dataclasses.replace(e, in_pr_body=True) for e in unmarked],
        updated_at=pr.state.now_iso(),
    ))
    return True


def run_describe(
    ctx: pr.context.ResolvedContext, *,
    force: bool = False, dry_run: bool = False,
    trail: Trail | None = None,
) -> int:
    """Revise the PR description if HEAD moved since the last pass.

    Callers must hold the target's run lock before calling this: it writes to
    GitHub (via `project_follow_ups`) and to the state file ahead of the
    HEAD-gate check below, and neither write is itself serialized. `main`
    acquires the lock via `run_lock.claim_for_process` before reaching here;
    a caller that skips `main` (tests, or a future direct caller) must claim
    it too rather than invoke this concurrently.
    """
    if not ctx.pr_number:
        core.log.info("No PR for this branch — nothing to describe")
        return 0

    wt_path = ctx.require_worktree()
    state = pr.state.load_state(ctx.target_dir)

    # Ahead of the HEAD gate: see project_follow_ups. Skipped on a dry run,
    # which must not write to GitHub.
    projection = Projection()
    if state and not dry_run:
        projection = project_follow_ups(ctx, state, trail=trail)
        if projection.moved:
            pr.state.save_state(ctx.target_dir, state)

    last_sha = state.describe.head_sha if state else ""
    if last_sha and last_sha == ctx.head_sha and not force:
        core.log.info(
            f"Description already written for {git.client.abbrev(ctx.head_sha)} — skipping")
        return 0

    template = core.pr_template.load(wt_path)
    # Reuse the projection's read when it made one, rather than asking gh for
    # the same body twice.
    fetched = (
        (projection.title, projection.body) if projection.fetched
        else _fetch_pr_body(ctx.repo, ctx.pr_number)
    )
    if fetched is None:
        if trail:
            trail.error("describe", "could not read the PR body",
                        data={"pr": ctx.pr_number})
        return 1
    title, body = fetched

    base = git.topology.default_branch(wt_path)
    commits = _git(wt_path, "log", "--oneline", f"origin/{base}..HEAD")
    changed_files = _git(wt_path, "diff", "--name-only", f"origin/{base}...HEAD")

    prompt = _build_prompt(
        template.text, template.found, title, body, commits, changed_files,
    )
    answer = agent.invoke.run_prompt(
        Phase.DESCRIBE, prompt,
        cwd=wt_path, usable=_usable_revision, task=SCRIPT,
        repo=ctx.repo, pr=str(ctx.pr_number),
    )
    if not answer.ok:
        if trail:
            trail.error("describe", "AI prompt failed", data={"exit_code": answer.exit_code})
        core.log.error("ai prompt failed")
        return 1

    raw = answer.text.strip()
    if raw == _NO_CHANGE:
        core.log.info("Description already matches the template — no change")
        _persist(wt_path, ctx, DescribeSummary(
            head_sha=ctx.head_sha, template_path=template.path,
            changed=False, updated_at=pr.state.now_iso(),
        ))
        return 0

    revised = _extract_description(raw)
    if revised is not None and state:
        # The AI replaces the body wholesale, so a block it did not reproduce is
        # a block this pass would have deleted. Re-injected rather than asked
        # for in the prompt: the prompt already asks for closing keywords
        # verbatim and `pr_preserve_close_refs` still exists as the backstop for
        # a one-line ref, so a multi-line block will not survive on instruction.
        revised = pr.follow_ups.project(revised, state.follow_ups.entries)
    if revised is None:
        # _usable_revision already passed, so this should not happen, but guard
        # against a caller that bypasses agent.invoke.run_prompt and so never
        # ran that check.
        if trail:
            trail.error("describe", "AI response missing extraction markers")
        core.log.error("ai response missing extraction markers — not posting to GitHub")
        return 1

    if dry_run:
        print(revised)
        return 0

    applied = _apply_body(ctx.repo, ctx.pr_number, revised)
    if not applied and core.publishing.enabled():
        # The gate was open and the write still failed — a real error, not a
        # draft. publishing.enabled() is what tells the two apart: _apply_body
        # returns False for both, and a draft is not a failure.
        if trail:
            trail.error("describe", "could not write the PR body",
                        data={"pr": ctx.pr_number})
        return 1

    if applied:
        core.log.info(f"Revised PR description against {template.path or 'the default template'}")
        if trail:
            trail.info("describe", "description revised",
                       data={"template": template.path, "head_sha": ctx.head_sha})
    # Recorded either way: a draft still reflects a real revision the AI
    # produced at this HEAD, so a repeated run before `--post` should not
    # re-earn the AI call — see the module docstring's commit-awareness note.
    _persist(wt_path, ctx, DescribeSummary(
        head_sha=ctx.head_sha, template_path=template.path,
        changed=True, updated_at=pr.state.now_iso(),
    ))
    return 0
