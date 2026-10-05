"""The describe pass: revise a PR's description against the repo's PR template.

Reads the PR body and the branch's commits, asks the agent for a revision in
the template's shape, validates what comes back, and applies or drafts it
under the publishing gate. Commit-aware: the HEAD it described is recorded,
with whether the result was published, so a repeated run against an unchanged
branch that was already published is a no-op — a draft does not count. Also
projects the branch's filed follow-up issues into the body.

`DescribeOptions` carries the hand overrides `pr create` also takes: a title or
body (the body template-checked as `pr create` checks one, and replacing the
AI revision) and `--closes` refs (the `pr.close_refs` contract). Any of them
bypasses the HEAD gate; a title or refs alone, at a HEAD already published, are
applied to the current body without another AI call. Closing references that the old body carried are re-appended
when the new body drops them, whoever wrote it. The range the AI reads is
measured against the PR's base (`pr.context.base_branch`), fetched first.

`cli.pr_describe` is the command over this — arguments, target resolution, the
run lock and the trail. Template resolution is `core.pr_template`.
"""

# doc-group: pr-state

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import agent.invoke
import gh.client
import git.client
import core.log
import core.pr_template
import core.publishing
import pr.context
import pr.follow_ups
import pr.state
from pr.close_refs import CloseRefError, append, normalise_all, preserve
from pr.create_content import extract, missing_sections, template_refusal
from core.phases import Phase
from pr.domains import DescribeSummary
from core.trail import Trail

# `git_remote` is a workbench-wide module, not an `ai/lib` one; see
# `pr.branch_sync` for the path arithmetic, which is the same here.
_WORKBENCH_LIB = Path(__file__).resolve().parent.parent.parent.parent / "lib"
if _WORKBENCH_LIB.is_dir() and str(_WORKBENCH_LIB) not in sys.path:
    sys.path.insert(0, str(_WORKBENCH_LIB))
import git_remote  # noqa: E402

# The binary a user runs and the trail records, which is not this module's own
# name. Spelled out rather than derived, so the shim can be renamed only by
# changing the name in both places at once.
SCRIPT = "pr-describe"

# The model says this, alone, when the body already conforms. Anything else is
# taken as the replacement body.
_NO_CHANGE = "DESCRIPTION_CURRENT"

# Extraction markers: the model wraps the revised description in these so we can
# parse and validate the response before posting it to GitHub. The name is what
# `pr.create_content.extract` takes; the two spellings are for the prompt.
_MARKER = "DESCRIPTION"
_DESCRIBE_BEGIN = f"<<<{_MARKER}>>>"
_DESCRIBE_END = f"<<<END_{_MARKER}>>>"


def _git(cwd: Path, *args: str) -> str:
    r = git.client.run(*args, cwd=cwd)
    if not r.ok:
        # Silent fallback is intentional — the prompt degrades gracefully to
        # "(none)" for commits/changed files rather than aborting the pass.
        # Log so a bad revision (e.g. unfetched origin/<base>) is diagnosable.
        core.log.warn(f"git {' '.join(args)} failed: {r.detail}")
        return ""
    return r.stdout.strip()


def _fetch_refusal(wt: Path, base: str) -> str:
    """Bring ``origin/<base>`` current; the ✗ refusal when that fails, else "".

    The range the AI reads is measured against ``origin/<base>``, which is
    otherwise only as fresh as the clone's last fetch — the same reasoning as
    ``pr.create``'s fetch ahead of its own measurements. A fetch that exits
    zero does not guarantee ``origin/<base>`` resolves afterwards (a remote
    with a non-default refspec, or a base deleted/renamed upstream), so this
    checks the ref too, as ``pr.create``'s ``_fetch_refusal``/``_base_refusal``
    pair does — otherwise `_git` in `_ai_revision` would silently degrade
    ``commits``/``changed_files`` to "" rather than refusing.
    """
    r = git.client.run("fetch", git_remote.GIT_REMOTE, base, "--quiet", cwd=wt)
    if not r.ok:
        return (f"✗ Could not fetch {git_remote.GIT_REMOTE}/{base}: "
                f"{r.detail or f'exit {r.returncode}'}")
    if not git_remote.remote_branch_ref_exists(base, cwd=str(wt)):
        return (f"✗ {git_remote.GIT_REMOTE}/{base} does not resolve — cannot measure "
                f"a description against a base that doesn't exist")
    return ""


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


def _usable_revision(text: str) -> bool:
    """The response must be the no-change sentinel or contain parseable markers."""
    t = text.strip()
    if t == _NO_CHANGE:
        return True
    return extract(t, _MARKER) is not None


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


@dataclasses.dataclass(frozen=True)
class DescribeOptions:
    """What the command line asked of a describe pass.

    ``title`` and ``body`` replace the PR's own; ``body`` also replaces the AI
    revision. ``closes`` is raw and normalised inside :func:`run_describe`.
    Any of the three is explicit intent, which the HEAD gate never skips.
    """

    force: bool = False
    dry_run: bool = False
    title: str = ""
    body: str = ""
    closes: tuple[str, ...] = ()

    @property
    def overrides(self) -> bool:
        return bool(self.title or self.body or self.closes)


def _apply_title(repo: str, pr_number: int, title: str) -> bool:
    """Replace the PR title, when publishing is on. Returns whether it landed.

    The same gate as :func:`_apply_body`, beside the write for the same reason.
    """
    if not core.publishing.enabled():
        core.publishing.draft(f"pr edit {repo}#{pr_number} --title", title)
        return False
    r = gh.client.run("pr", "edit", str(pr_number), "--repo", repo, "--title", title)
    if not r.ok:
        core.log.error(f"gh pr edit --title failed: {r.detail}")
        return False
    return True


def _ai_revision(
    ctx: pr.context.ResolvedContext, wt_path: Path, template, title: str, body: str,
    *, trail: Trail | None,
) -> str | None:
    """The AI's revision of *body*, *body* itself when it says no change, None on failure.

    The range is measured against the PR's own base, so a PR targeting
    ``release`` is described by what it adds to ``release`` — and the base is
    fetched first, so the range is not measured against a stale ref.
    """
    base = pr.context.base_branch(ctx, cwd=str(wt_path), trail=trail)
    refusal = _fetch_refusal(wt_path, base)
    if refusal:
        print(refusal, file=sys.stderr, flush=True)
        if trail:
            trail.error("describe", "could not fetch the base", data={"base": base})
        return None
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
        return None

    raw = answer.text.strip()
    if raw == _NO_CHANGE:
        return body
    revised = extract(raw, _MARKER)
    if revised is None:
        # _usable_revision already passed, so this should not happen, but guard
        # against a caller that bypasses agent.invoke.run_prompt and so never
        # ran that check.
        if trail:
            trail.error("describe", "AI response missing extraction markers")
        core.log.error("ai response missing extraction markers — not posting to GitHub")
    return revised


def _finish_body(revised: str, old_body: str, entries, closes: tuple[str, ...]) -> str:
    """Re-inject what a wholesale replacement would otherwise have deleted.

    The follow-up block, then every close ref the old body carried, then the
    `--closes` refs. Re-injected rather than asked for in the prompt: a hand
    body was never prompted, and a model told to keep a line can still drop it.
    """
    revised = pr.follow_ups.project(revised, entries)
    kept = preserve(old_body, revised)
    if kept.linked:
        core.log.ok("Preserved existing issue link(s): "
                    + " ".join(f"Closes {ref}" for ref in kept.linked))
    linked = append(kept.body, closes)
    if linked.linked:
        core.log.ok("Linked for auto-close on merge: "
                    + " ".join(f"Closes {ref}" for ref in linked.linked))
    return linked.body


def run_describe(
    ctx: pr.context.ResolvedContext, opts: DescribeOptions = DescribeOptions(), *,
    trail: Trail | None = None,
) -> int:
    """Revise the PR description if HEAD moved since the last pass.

    `opts.title`, `opts.body` and `opts.closes` are explicit requests and run
    whatever HEAD says; a hand body replaces the AI revision outright, after
    the same template check `pr create` applies.

    Callers must hold the target's run lock before calling this: it writes to
    GitHub (via `project_follow_ups`) and to the state file ahead of the
    HEAD-gate check below, and neither write is itself serialized. `main`
    acquires the lock via `run_lock.claim_for_process` before reaching here;
    a caller that skips `main` (tests, or a future direct caller) must claim
    it too rather than invoke this concurrently.
    """
    if not ctx.pr_number:
        # An override is a request to change a PR, and there is none to change.
        # Without one this stays a no-op, which is what lets `pr fix` call
        # describe unconditionally.
        if opts.overrides:
            print(f"✗ No PR found for {ctx.branch} — --title/--body/--closes need one; "
                  "use pr create", file=sys.stderr, flush=True)
            return 1
        core.log.info("No PR for this branch — nothing to describe")
        return 0

    wt_path = ctx.require_worktree()
    try:
        closes = normalise_all(opts.closes, wt_path)
    except CloseRefError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    state = pr.state.load_state(ctx.target_dir)

    # Ahead of the HEAD gate: see project_follow_ups. Skipped on a dry run,
    # which must not write to GitHub.
    projection = Projection()
    if state and not opts.dry_run:
        projection = project_follow_ups(ctx, state, trail=trail)
        if projection.moved:
            pr.state.save_state(ctx.target_dir, state)

    # Only a published run counts as done: a draft at this HEAD wrote nothing,
    # so the `--post` that follows it must not be skipped.
    last_sha = state.describe.head_sha if state else ""
    published_at_head = bool(
        last_sha and last_sha == ctx.head_sha and state.describe.published)
    if published_at_head and not (opts.force or opts.overrides):
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

    if opts.body:
        missing = missing_sections(template, opts.body)
        if missing:
            print(template_refusal(template, missing), file=sys.stderr, flush=True)
            return 1
        revised = opts.body
    elif published_at_head and not opts.force:
        # Only --title/--closes were asked for, against a body already
        # published for this HEAD: the revision would be the same AI call the
        # gate exists to save, so they are applied to the body as it stands.
        revised = body
    else:
        revised = _ai_revision(ctx, wt_path, template, title, body, trail=trail)
        if revised is None:
            return 1

    entries = state.follow_ups.entries if state else []
    revised = _finish_body(revised, body, entries, closes)

    # Measured against what the PR holds once the follow-ups are in it, not
    # against the body as read. In a draft — or on a dry run, which skips the
    # projection pass — the read body lacks the follow-up block, so a no-change
    # answer would otherwise come back from _finish_body as a revision and be
    # drafted, and recorded as one, a second time.
    projected = pr.follow_ups.project(body, entries) if entries else body
    if revised.strip() == projected.strip() and not opts.title:
        core.log.info("Description already matches the template — no change")
        if not opts.dry_run:
            _persist(wt_path, ctx, DescribeSummary(
                head_sha=ctx.head_sha, template_path=template.path,
                changed=False, published=True, updated_at=pr.state.now_iso(),
            ))
        return 0

    if opts.dry_run:
        if opts.title:
            print(opts.title)
            print()
        print(revised)
        return 0

    written = _write(ctx, revised, body, opts.title, template, trail=trail)
    if not written.ok:
        return 1
    # Recorded either way, with whether it reached the PR: _write returned
    # True, so with the gate open every write it attempted landed. A draft
    # records published=False, which the HEAD gate reads as not yet done.
    # `changed` is the comparison `_write` itself acted on — threaded through
    # rather than recomputed, so persisting can never drift from what `_write`
    # did. A title-only override against a body that did not move (e.g. a
    # published HEAD) must not be reported as a body change.
    _persist(wt_path, ctx, DescribeSummary(
        head_sha=ctx.head_sha, template_path=template.path,
        changed=written.changed, published=core.publishing.enabled(),
        updated_at=pr.state.now_iso(),
    ))
    return 0


@dataclasses.dataclass(frozen=True)
class WriteResult:
    """What `_write` did: whether it failed, and whether the body moved.

    ``ok`` is False only on a real failure — a draft is not one, since both
    writes return False with the gate closed, and `core.publishing.enabled()`
    is what tells a draft from a rejected edit. ``changed`` is `_write`'s own
    revised/body comparison, for the caller to persist without recomputing it
    and risking the two drifting apart.
    """

    ok: bool
    changed: bool


def _write(
    ctx: pr.context.ResolvedContext, revised: str, body: str, title: str, template,
    *, trail: Trail | None,
) -> WriteResult:
    """Apply the body (when it changed) and the title (when given). See `WriteResult`."""
    publishing = core.publishing.enabled()
    changed = revised.strip() != body.strip()
    applied = changed and _apply_body(ctx.repo, ctx.pr_number, revised)
    if changed and not applied and publishing:
        return WriteResult(_write_failed(trail, ctx, "could not write the PR body"), changed)
    if applied:
        core.log.info(
            f"Revised PR description against {template.path or 'the default template'}")
        if trail:
            trail.info("describe", "description revised",
                       data={"template": template.path, "head_sha": ctx.head_sha})
    if title and not _apply_title(ctx.repo, ctx.pr_number, title) and publishing:
        return WriteResult(_write_failed(trail, ctx, "could not write the PR title"), changed)
    return WriteResult(True, changed)


def _write_failed(trail: Trail | None, ctx: pr.context.ResolvedContext, what: str) -> bool:
    """Record a rejected write on the trail; always False, for `_write` to return."""
    if trail:
        trail.error("describe", what, data={"pr": ctx.pr_number})
    return False
