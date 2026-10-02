"""Open a pull request for the current branch — what ``pr create`` does.

Ports the go-task create flow that lived in ``lib/ai/pr.sh``: preflight
(an already-open PR, default-branch and base refusals, no commits ahead of
the base (D7), the ``--closes`` contract), the publishing token, the nesting
gate, the branch push, content generation and ``gh pr create``.
``--dry-run`` stops after the content is generated and prints it; it runs no
gate, pushes nothing and never reaches ``gh``.

The order is the contract. Every refusal that costs nothing comes before the
nesting gate, the gate comes before anything leaves the machine, and the push
comes before the AI call so a refused push never pays for one. ``--base`` is
always passed to ``gh`` (D5): the base the gate and the content measured is
the base the PR targets.

Progress and ✗ lines go to stderr, like ``pr.branch_sync``'s; stdout carries
only the answer — the PR URL, or the dry-run preview — so a caller can capture
it.
"""

# doc-group: publishing

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import contextlib

import config.workbench_config
import core.proc
import core.timeouts
from core.trail import Trail
import gh.client
import git.client
import git.push
import git.topology
import pr.branch_sync
import pr.create_content
import pr.gh_token
from pr.branch_sync import SyncOutcome
from pr.close_refs import CloseRefError, normalise, stage
from pr.context import ResolvedContext
from pr.create_content import ContentError, ContentRequest

# `git_remote` is a workbench-wide module rather than an `ai/lib` one; see
# `pr.branch_sync` for the path arithmetic, which is the same here.
_WORKBENCH_LIB = Path(__file__).resolve().parent.parent.parent.parent / "lib"
if _WORKBENCH_LIB.is_dir() and str(_WORKBENCH_LIB) not in sys.path:
    sys.path.insert(0, str(_WORKBENCH_LIB))
import git_remote  # noqa: E402

GIT_REMOTE = git_remote.GIT_REMOTE

# Dropped from every git/validate-nesting child's environment. A hook that
# invoked `pr create` could have either set, and with either set a `git -C`
# or a `rev-parse --show-toplevel` answers for the hook's repo rather than
# the worktree this run was asked to act on — see `pr.create_content._git_env`,
# which this mirrors.
_GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")

# The first `https://…/pull/<n>` in gh's output. The host is free so an
# enterprise or self-hosted URL is reported; the `/pull/<n>` anchor is what
# keeps `https://githubstatus.com` and an `/issues/<n>` link from matching.
PR_URL_RE = re.compile(r"https://\S+/\S+/pull/\d+")

# validate-nesting's "could not run" status, as distinct from "found
# violations". Spelled where the gate reads it, since the script owns it.
_NESTING_COULD_NOT_RUN = 2

# Outcomes `sync_branch` did not already print through its log callback. The
# pushing outcomes announced themselves before the push ran; printing their
# message again would duplicate the line.
_UNEMITTED = frozenset({
    SyncOutcome.UP_TO_DATE, SyncOutcome.BEHIND,
    SyncOutcome.DIVERGED, SyncOutcome.FAILED,
})


@dataclass(frozen=True)
class CreateOptions:
    """What ``pr create``'s flags asked for. ``closes`` is raw, normalised here."""

    draft: bool = False
    no_verify: bool = False
    dry_run: bool = False
    base: str = ""
    title: str = ""
    body: str = ""
    issue: str = ""
    closes: tuple[str, ...] = ()


def _say(msg: str) -> None:
    """A progress or ✗ line. The text carries its own →/✓/✗ prefix."""
    print(msg, file=sys.stderr, flush=True)


def _git_env() -> dict[str, str]:
    """The process environment with inherited git overrides dropped.

    See `_GIT_ENV_DROP`: a run launched from a hook that exports `GIT_DIR` or
    `GIT_WORK_TREE` would otherwise have every git call here, and the
    `validate-nesting` subprocess below, answer for the hook's repo instead
    of the worktree `pr create` was asked to act on.
    """
    env = os.environ.copy()
    for name in _GIT_ENV_DROP:
        env.pop(name, None)
    return env


@contextlib.contextmanager
def _env_cleared():
    """Drop `_GIT_ENV_DROP` from the process environment for the duration.

    `git_remote`'s helpers (and `git.topology.default_branch`, which is one of
    them) take no `env=` — by its own docstring's design, which leaves
    "unsetting it... to the caller that knows whether it owns the process".
    `pr create` does, so this clears the override process-wide for the one
    call it wraps and restores it after, rather than letting a hook's
    `GIT_DIR` make the default-branch or base-ref lookup answer for the
    hook's repo instead of `wt`.
    """
    saved = {name: os.environ.pop(name, None) for name in _GIT_ENV_DROP}
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value


def _base_refusal(base: str, default: str, *, explicit: bool) -> str:
    lines = [
        f"✗ {GIT_REMOTE}/{base} does not resolve — cannot open a PR against "
        f"a base that doesn't exist",
        f"→ Fix with: git fetch {GIT_REMOTE}",
    ]
    # Only a guessed base can be wrong about which branch is the default; a
    # base the operator named is wrong for a reason set-head cannot fix.
    if not explicit:
        lines.append(
            f"→ If {default} is a guess and the real default branch differs, "
            f"also run: git remote set-head {GIT_REMOTE} -a"
        )
    return "\n".join(lines)


def _closes(raw: tuple[str, ...], wt: Path) -> tuple[str, ...]:
    """Normalise every ``--closes`` value; raises CloseRefError on the first bad one.

    The provider decides what a ref may be, so a config that cannot be read
    raises ConfigError rather than defaulting: a guessed provider would accept
    or refuse refs the operator's tracker would not. Read only when there are
    refs to judge, so a broken config costs nothing to a create without any.
    """
    if not raw:
        return ()
    provider = config.workbench_config.load_config(wt).issues.provider
    refs: list[str] = []
    for value in raw:
        refs = stage(refs, normalise(value, provider))
    return tuple(refs)


def _nesting_gate(wt: Path, base: str) -> bool:
    """Run ``validate-nesting`` over the diff from ``origin/<base>``; True when clean.

    A gate that cannot start is reported as one that could not run — the
    same message as its own exit 2 — rather than as a pass.
    """
    argv = ["validate-nesting", "--diff", f"{GIT_REMOTE}/{base}", "--quiet"]
    try:
        # Unbounded: the gate parses every file the diff touches, so its cost is the input's.
        r = subprocess.run(
            argv, cwd=wt, capture_output=True, text=True,
            timeout=core.timeouts.UNBOUNDED, env=_git_env(),
        )
        status, output = r.returncode, (r.stdout or "") + (r.stderr or "")
    except OSError as exc:
        status, output = _NESTING_COULD_NOT_RUN, str(exc)
    if status == 0:
        return True
    if status == _NESTING_COULD_NOT_RUN:
        _say("✗ Nesting check could not run — fix before creating PR")
    else:
        _say("✗ New code introduces nesting violations — fix before creating PR")
    _say("")
    _say(output.rstrip("\n"))
    return False


def _sync(wt: Path, branch: str, *, no_verify: bool, trail: Trail | None) -> bool:
    result = pr.branch_sync.sync_branch(
        wt, branch, no_verify=no_verify, log=_say, trail=trail,
    )
    # A failed push's message is its output, which `git.push.report` prints
    # with the headline and resume command; printing it here too doubled it.
    if result.outcome is SyncOutcome.FAILED and result.push is not None:
        git.push.report(result.push, wt)
    elif result.outcome in _UNEMITTED:
        _say(result.message)
    return result.ok


def _ahead_refusal(wt: Path, branch: str, base: str) -> str:
    """The D7 ✗ line when *branch* has nothing over ``origin/<base>``, else "".

    Asked in preflight so a PR with nothing in it is refused before the token,
    the gate, the push or the AI call — including when ``--title`` and
    ``--body`` leave content generation nothing to read.
    """
    remote_base = f"{GIT_REMOTE}/{base}"
    argv = ("rev-list", "--count", f"{remote_base}..HEAD")
    r = git.client.run(*argv, cwd=wt, env=_git_env())
    count = r.stdout.strip()
    if not r.ok or not count.isdigit():
        detail = r.detail or f"exit {r.returncode}"
        return f"✗ git {' '.join(argv)} failed: {detail}"
    if int(count) == 0:
        return f"✗ No commits on {branch} ahead of {remote_base} — nothing to open a PR for"
    return ""


def _preflight(
    wt: Path, branch: str, default: str, opts: CreateOptions, pr_number: int | None,
) -> tuple[str, ...] | None:
    """Every refusal that needs no network write, in order. None means refused.

    Returns the normalised ``--closes`` refs, the one thing the checks produce.
    The default-branch refusal is keyed on the repo's default, not the target
    base: running from trunk is wrong whatever the PR would target. An open PR
    refuses a dry run too — the preview would describe a PR nobody can open.
    """
    if pr_number:
        _say(f"✗ PR #{pr_number} already exists for {branch} — use pr describe to revise it")
        return None
    if branch == default:
        _say(f"✗ PR operations cannot be run from the {default} branch")
        return None
    base = opts.base or default
    with _env_cleared():
        base_exists = git_remote.remote_branch_ref_exists(base, cwd=str(wt))
    if not base_exists:
        _say(_base_refusal(base, default, explicit=bool(opts.base)))
        return None
    refusal = _ahead_refusal(wt, branch, base)
    if refusal:
        _say(refusal)
        return None
    try:
        return _closes(opts.closes, wt)
    except (CloseRefError, config.workbench_config.ConfigError) as exc:
        _say(str(exc) if isinstance(exc, CloseRefError) else f"✗ {exc}")
        return None


def _publishable(
    wt: Path, base: str, branch: str, opts: CreateOptions, trail: Trail | None,
) -> bool:
    """Token, nesting gate, push — the steps a dry run skips."""
    try:
        pr.gh_token.use_for_publishing(wt)
    except pr.gh_token.TokenNotConfigured as exc:
        print(exc.guidance, file=sys.stderr, flush=True)
        return False
    except OSError as exc:
        # The same wording as `pr.gh_token.main`: an unreadable credentials
        # file is a fault to name, not a traceback.
        path = exc.filename or "a GH_TOKEN config file"
        _say(f"✗ Could not read {path}: {exc.strerror or exc}")
        return False
    if not _nesting_gate(wt, base):
        return False
    return _sync(wt, branch, no_verify=opts.no_verify, trail=trail)


def _gh_create(wt: Path, title: str, body: str, base: str, draft: bool) -> int:
    _say("→ Creating PR...")
    # NETWORK, the tier gh.client keys on this argv: latency-bound, so a breach is a hang,
    # but the write may have landed — hence the timeout's own message below.
    r = gh.client.run(
        "pr", "create", "--title", title, "--body-file", "-",
        "--assignee", "@me", "--base", base,
        *(["--draft"] if draft else []),
        cwd=wt, input_text=body,
    )
    output = r.combined_output
    if r.returncode == core.proc.TIMEOUT_RETURNCODE:
        _say("✗ gh pr create timed out — check gh pr view before retrying")
        return 1
    if not r.ok:
        _say(f"✗ PR creation failed (gh exited {r.returncode})")
        _say(output.rstrip("\n"))
        return 1
    match = PR_URL_RE.search(output)
    if match is None:
        _say("✗ PR creation failed — gh reported success but printed no pull request URL")
        _say(output.rstrip("\n"))
        return 1
    _say("✓ Pull request created")
    print(match.group(0), flush=True)
    return 0


def run_create(
    ctx: ResolvedContext, opts: CreateOptions, *, trail: Trail | None = None,
) -> int:
    """Open the PR *opts* describes for ``ctx.branch``, or preview it. 0 on success.

    *trail* is the dispatch's, threaded to the push so it is recorded there.
    """
    wt = ctx.worktree_root
    branch = ctx.branch
    if wt is None:
        _say(f"✗ {branch} has no checkout — run from its worktree or pass --repo-dir")
        return 1

    with _env_cleared():
        default = git.topology.default_branch(wt)
    closes = _preflight(wt, branch, default, opts, ctx.pr_number)
    if closes is None:
        return 1
    base = opts.base or default

    if not opts.dry_run and not _publishable(wt, base, branch, opts, trail):
        return 1

    _say(f"→ Analyzing changes: {branch}")
    req = ContentRequest(
        branch=branch, base=base, issue=opts.issue,
        title=opts.title, body=opts.body, closes=closes,
    )
    try:
        content = pr.create_content.generate(wt, req, repo=ctx.repo, log=_say)
    except ContentError as exc:
        _say(str(exc))
        return 1
    _say("✓ Content ready")
    for ref in content.already:
        _say(f"✓ Already linked: Closes {ref}")
    if content.linked:
        staged = " ".join(f"Closes {ref}" for ref in content.linked)
        _say(f"✓ Linked for auto-close on merge: {staged}")

    if opts.dry_run:
        print("→ PR Title:")
        print(f"   {content.title}")
        print()
        print("→ PR Description:")
        print(content.body, flush=True)
        return 0

    return _gh_create(wt, content.title, content.body, base, opts.draft)
