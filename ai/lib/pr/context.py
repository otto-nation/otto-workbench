"""Shared PR context resolution.

Resolves repo, branch, PR number, worktree root, and HEAD SHA once
per invocation. Replaces the duplicated discovery logic ci-check,
review-threads, and the review pipeline each carried a copy of.

How much of that a command wants is one of three axes every `pr` subcommand
declares in its `_COMMANDS` entry in `ai/bin/pr`. They are separate
because they routinely disagree:

| Axis | What it decides |
|---|---|
| **depth** | `ContextDepth.NONE` resolves nothing at all; `LOCAL` resolves from git alone; `REMOTE` adds the `gh` calls that name the repo and the PR |
| **fetch** | whether the worktree is fetched and fast-forwarded first |
| **lock** | whether the target's `run.lock` is held for the whole run |

| Command | Depth | Fetch | Lock |
|---|---|---|---|
| `create` | remote | no | yes |
| `status` | local | no | **no** |
| `ci` | remote | yes | yes |
| `review` | remote | yes | yes |
| `review --summary` / `--post` / `--repair` / `--recover` | remote | **no** | yes |
| `review --list` | **none** | no | **no** |
| `comments` | remote | yes | yes |
| `fix` | remote | yes | yes |
| `rebase` | remote | no | yes |
| `describe` | remote | yes | yes |
| `gc` | remote | no | yes |

`review` is the one command whose need its arguments decide, which is why its
`_COMMANDS` entry holds a resolver rather than a `Need`. The fetch is the line
between its two halves: a bare `pr review` is about to review the branch, so it
wants the branch current, while every mode flag acts on a review that already
exists at the commit that review describes. Fast-forwarding under one of those
would leave `--summary` and `--post` reporting a review of a commit the
worktree no longer sits on, and would push `--recover` off the SHA it then has
to pin a throwaway worktree back to.

`rebase` is the reason the axes are separate: it needs `gh` to name its PR and
does its own fetch, so a single "is this command remote?" flag would either
strand it or reset the worktree under it.

A command that declares nothing fails at import rather than silently picking up
a default — `_validate_needs` is the check, and it is what makes adding a
command a one-line edit in one place.

`status` is the only local one. It reads `state.json` and the worktree's push
state, and needs neither `gh repo view` nor `gh pr view` to do it: with no
`state.json` yet, the header names the repo from the origin-derived label
behind the repo key (`acme/widget`) rather than from `gh`. An explicit
`--pr <n>` escalates it to remote anyway — a PR number names a branch only `gh`
can report, and the branch is half the target key.

`review --list` is the only `NONE` one, and that is not "resolve less" — it is
"there is nothing to resolve". The listing answers from the user's own state
root, so it has no repo, no branch, and no target, and unlike `LOCAL` it works
from a directory that is not a git repository at all. `--pr` does not escalate
it: there is no target for a PR number to name at that depth, so honouring one
would spend a `gh` call on a value the handler never reads.

`review --list` is also the one invocation that writes no trail at all.
Resolving nothing and holding no lock is the shape of a query rather than of an
action, and the listing exists to be polled: the two records a dispatch writes
cost more than the query itself, and they land in the file every `otto-log`
query then reads. The exemption is read off these same three axes — `Need`
carries no trail flag of its own for a command to add itself to.
"""

# doc-group: pr-state

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import NoReturn

from gh import client as gh_client
from git import topology as git_topology
from core import log
from core.trail import Trail, tdecision
from pr import target as pr_target
from core import timeouts
# Re-exported rather than called through the module: this file's own call sites
# read as `failure_message(...)`, and proc.py is stdlib-only so the import costs
# consumers nothing.
from core.proc import failure_message  # noqa: F401

_PR_URL_RE = re.compile(r"/pull/(\d+)")
_PR_NUMBER_RE = re.compile(r"^\d+$")


def is_pr_ref(s: str) -> bool:
    """True if *s* looks like a PR number or GitHub PR URL."""
    return bool(_PR_URL_RE.search(s) or _PR_NUMBER_RE.match(s))


def classify_target(target: str) -> tuple[str | None, str | None]:
    """Classify an ambiguous positional as (pr, branch).

    Returns a 2-tuple where exactly one element is the input string
    and the other is None.
    """
    if is_pr_ref(target):
        return target, None
    return None, target


@dataclass(frozen=True)
class BranchPR:
    """The open PR for a branch, as far as ``gh`` would say.

    Both fields come from one call. They are returned together rather than as a
    number alone because every caller that wants to know a branch's PR also
    wants to know what it targets, and asking twice is a second round trip for
    a field the first response already carried.

    All-empty is the routine answer: no PR, no ``gh``, no auth, no network. A
    caller reads it as "the tracker has nothing to say", never as "there is no
    PR" — see :func:`pr_number_if_reachable`.
    """

    number: int | None = None
    base: str = ""


@dataclass(frozen=True)
class PRHead:
    """A PR's head branch and SHA, or the reason ``gh`` could not report them.

    Both halves come from one API call because the SHA is what a PR target's
    state must be stamped with — reading it from the caller's HEAD stamps the
    state with the repo root's SHA instead. The base rides along on the same
    call for the branch this PR targets, which is what a stacked branch's diff
    has to be measured against.
    """
    branch: str = ""
    sha: str = ""
    # What the PR targets per GitHub, or "" when the read did not carry it.
    # Not part of `resolved`: a caller needs the head to proceed at all, where
    # an unknown base has a fallback ladder behind it.
    base: str = ""
    # Ready-to-print reason the call did not answer, quoting gh's stderr. The
    # caller decides an unresolved head is fatal, so the caller is the one that
    # has to be able to say why. Empty when the head resolved.
    reason: str = ""

    @property
    def resolved(self) -> bool:
        """True only with the head in hand — a partial answer is a failure.

        The base is deliberately not required: it has a fallback ladder behind
        it, and refusing a PR whose head both fields' callers can use would
        turn a degraded answer into a fatal one.
        """
        return bool(self.branch and self.sha)


# Where an unresolved context points its target. Nothing is ever created here:
# every path component after /dev/null fails with ENOTDIR, which is the point.
_UNRESOLVED_TARGET = Path("/dev/null/pr-context-unresolved")


@dataclass(frozen=True)
class ResolvedContext:
    """Immutable PR context resolved once at command entry."""
    repo: str
    branch: str
    pr_number: int | None
    worktree_root: Path | None
    head_sha: str
    current_branch: str | None = None
    # The branch GitHub says this PR targets, or "" when it did not say. Read
    # off the same call that found the PR number, so it costs nothing extra.
    #
    # Empty means "unknown", never "there is no base" — the same contract
    # `pr_number` carries, and for the same reason: an absent, unauthenticated
    # or rate-limited `gh` is indistinguishable here from a branch with no PR.
    # Consumers pass it to `base_branch()` rather than reading it directly,
    # which is what turns an unknown into a derived or defaulted answer.
    base: str = ""
    # The forge `repo` is served from, for rendering a link that resolves on it.
    # Empty means no host was discoverable, which reads as public GitHub — the
    # behaviour every URL builder had before this field existed, so an
    # unthreaded caller is unchanged rather than broken.
    #
    # Not folded into `repo`: that string is the `--repo` argument and the
    # GraphQL `owner/name`, and a host in it would reach both.
    host: str = ""
    # Where the run's bookkeeping lives, as opposed to where git runs. Keyword-
    # only and required: a caller that forgets it would silently get a context
    # whose state and lock point nowhere.
    target_dir: Path = field(kw_only=True)

    @classmethod
    def unresolved(cls) -> "ResolvedContext":
        """A context for a command that asked for nothing to be resolved.

        Every field is empty because nothing was consulted — no git, no ``gh``,
        no worktree. Handed to ``ContextDepth.NONE`` commands, which answer from
        the user's state root and have no target of their own.

        ``target_dir`` is a path under ``/dev/null`` rather than the empty path,
        which names the current directory: a handler that reads it despite
        declaring it needed nothing fails at its first open instead of writing a
        run's bookkeeping wherever the caller happened to be standing.
        """
        return cls(
            repo="", branch="", pr_number=None, worktree_root=None, head_sha="",
            target_dir=_UNRESOLVED_TARGET,
        )

    def require_worktree(self) -> Path:
        """The worktree root, or exit 1 naming what to do about its absence.

        ``worktree_root`` is legitimately None — a bare repo with no worktree
        checked out on the branch has nowhere to read state from or run git in.
        Every consumer that cannot work without one calls this instead of
        dereferencing the field, so the failure is one message here rather than
        a TypeError, a ``FileNotFoundError: 'None'``, or the string "None"
        reaching ``git -C`` several frames later.

        Consumers that can degrade (ci-check without --fix, pr create, pr gc)
        read the field directly and are visibly opted out.
        """
        if self.worktree_root is None:
            log.error(
                f"No worktree for {self.branch!r} — "
                f"run: wt switch {self.branch} (or pass --repo-dir)"
            )
            sys.exit(1)
        return self.worktree_root


class ContextDepth(Enum):
    """How far a caller needs context resolved — which rung of the ladder runs.

    The values name the deepest source consulted: NONE consults nothing, LOCAL
    is git alone, REMOTE adds ``gh``. Independent of whether the caller then
    fetches (``update_to_remote``) or takes the run lock — a command declares
    all three separately.

    NONE is not "resolve less"; it is "there is nothing to resolve". A command
    at this depth answers from the user's own state root, so it has no repo, no
    branch, and no target — and, unlike LOCAL, it works from a directory that
    is not a git repository at all.
    """

    NONE = "none"
    LOCAL = "local"
    REMOTE = "remote"


def resolve_at(
    depth: ContextDepth,
    *,
    pr: str | None = None,
    branch: str | None = None,
    repo_dir: str | None = None,
) -> ResolvedContext:
    """Resolve at *depth*, escalating to REMOTE when a ``--pr`` demands it.

    A PR number names a branch only ``gh`` can report, and the branch is half
    the run's target key — resolving locally anyway would take the lock and
    write state under whatever branch happens to be checked out. So an explicit
    PR reference wins over the declared depth rather than being ignored.

    NONE does not escalate. There is no target for a ``--pr`` to name at that
    depth, so honouring one would spend a ``gh`` call on a value the handler
    never reads — and a command declared read-only would make a network call
    because of a flag the caller passed by habit.
    """
    if depth is ContextDepth.NONE:
        return ResolvedContext.unresolved()
    if depth is ContextDepth.LOCAL and pr is None:
        return resolve_local(branch=branch, repo_dir=repo_dir)
    return resolve(pr=pr, branch=branch, repo_dir=repo_dir)


def resolve(
    *,
    pr: str | None = None,
    branch: str | None = None,
    repo_dir: str | None = None,
) -> ResolvedContext:
    """Resolve PR context from arguments and git state.

    Resolution order:
    1. --pr given: derive branch and repo from the PR.
    2. --branch given: use directly, detect repo from remote.
    3. Neither: detect everything from current git state.

    Raises ValueError if both pr and branch are given.
    """
    if pr is not None and branch is not None:
        raise ValueError("--pr and --branch are mutually exclusive")

    cwd = repo_dir

    worktree_root, cwd = _resolve_worktree(cwd, pr=pr, branch=branch)

    repo = detect_repo(cwd)

    if pr:
        pr_number = _parse_pr_input(pr)
        head = _pr_head(repo, pr_number)
        if not head.resolved:
            # PRHead's contract says an unresolved head carries a reason, but
            # the guard outlives the contract: a partial result must never fall
            # through to the caller's own branch, reason or no reason.
            log.error(head.reason
                      or f"Cannot resolve the head branch of {repo}#{pr_number}")
            log.dim("pr keys a run's state and lock on its target branch and "
                    "stamps state with its head SHA")
            sys.exit(1)
        branch_name = head.branch
        # The PR's HEAD, not the caller's: state written for this run belongs to
        # the PR, and the caller may be sitting on an unrelated branch.
        head_sha = head.sha
        base = head.base
    elif branch:
        branch_name = git_topology.resolve_branch(branch, cwd)
        found = _pr_from_branch(repo, branch_name)
        pr_number, base = found.number, found.base
    else:
        branch_name = git_topology.current_branch(cwd)
        found = _pr_from_current(cwd)
        pr_number, base = found.number, found.base

    if not pr:
        head_sha = _head_sha(cwd) if worktree_root else ""

    current = git_topology.current_branch_quiet(cwd) if worktree_root else None

    # One read for both: the key and the host come off the same origin, so this
    # cannot name one repo's directory and another's forge.
    identity = _target_identity(cwd)

    return ResolvedContext(
        repo=repo,
        branch=branch_name,
        pr_number=pr_number,
        worktree_root=worktree_root,
        head_sha=head_sha,
        current_branch=current,
        base=base,
        host=identity.host,
        target_dir=pr_target.target_dir(identity.key, branch_name),
    )


def resolve_local(
    *,
    branch: str | None = None,
    repo_dir: str | None = None,
) -> ResolvedContext:
    """Resolve as much context as git alone can answer — no ``gh``, no network.

    The shallow rung of ``ContextDepth``, for commands that only need to find
    the run's target directory and its worktree. Two consequences the caller
    signs up for by asking for this depth:

    * ``pr_number`` is always None and ``repo`` is the canonical form behind the
      repo key (``acme/widget``), not ``gh``'s ``owner/repo`` — see
      ``pr_target.RepoIdentity``. A caller that wants the branch's PR without
      giving up this rung's no-network guarantee asks for it separately, with
      ``pr_number_if_reachable``.
    * A bare repo hands back an existing worktree but never creates one, so
      ``worktree_root`` can be None where ``resolve`` would have made a
      checkout. Commands needing one call ``require_worktree``.

    ``target_dir`` is identical to what ``resolve`` computes for the same
    branch: both key on ``(origin repo key, branch)``, neither reads the network.
    """
    cwd = repo_dir

    worktree_root, cwd = _resolve_worktree(
        cwd, pr=None, branch=branch, create_missing=False,
    )

    branch_name = (
        git_topology.resolve_branch(branch, cwd) if branch
        else git_topology.current_branch(cwd)
    )

    identity = _target_identity(cwd)

    return ResolvedContext(
        repo=identity.label,
        pr_number=None,
        branch=branch_name,
        worktree_root=worktree_root,
        head_sha=_head_sha(cwd) if worktree_root else "",
        current_branch=git_topology.current_branch_quiet(cwd) if worktree_root else None,
        host=identity.host,
        target_dir=pr_target.target_dir(identity.key, branch_name),
    )


def base_branch(
    ctx: ResolvedContext,
    *,
    override: str = "",
    known: str | None = None,
    cwd: str | None = None,
    trail: Trail | None = None,
) -> str:
    """The branch *ctx*'s branch should be measured against, most authoritative first.

    One ladder, so a review, a rebase and a supersession check on the same
    branch all name the same base. A branch stacked on another feature branch
    is measured against that parent rather than against the trunk, which is
    otherwise wrong in a way nothing reports: the parent's commits are read as
    this branch's own, and every finding about them is a finding about code the
    author did not write here.

    The rungs, and why they are in this order:

    1. *override* — the operator said so, and no derivation outranks that.
    2. GitHub's ``baseRefName``, normally ``ctx.base``. An open PR states its
       base as a fact; the rungs below only infer one. A caller that read the
       same fact from somewhere else — ``pr rebase`` has it on a snapshot that
       collected six fields in one call — passes it as *known* rather than
       forging a context to carry it. ``known=""`` is "that source had nothing
       to say", which falls through; the default of None reads ``ctx.base``.
    3. :func:`git.topology.stack_parent` — local ancestry, for the stack whose
       parent has no PR yet. Answers "" for an ordinary branch off the trunk,
       which is what makes rung 4 the common path rather than a fallback.
    4. the repo's default branch — what every caller used before this existed.

    Returns a bare branch name, never a ref: callers spell ``origin/<name>``
    themselves. That is deliberate at rung 3, where the name is derived from a
    local ref whose position may be stale — it nominates a base without being
    the commit anything is measured against.
    """
    def decide(base: str, reason: str) -> str:
        tdecision(trail, "base_branch", f"measuring against origin/{base}", reason=reason)
        return base

    if override:
        return decide(override, "explicitly set")
    stated = ctx.base if known is None else known
    if stated:
        return decide(stated, f"PR #{ctx.pr_number} targets {stated}")

    where = cwd or (str(ctx.worktree_root) if ctx.worktree_root else None)
    default = git_topology.default_branch(where)
    parent = git_topology.stack_parent(where, default=default)
    if parent:
        return decide(parent, "nearest local ancestor of HEAD — this branch is stacked")
    return decide(default, "no stack parent and no PR base — the repo's default branch")


def pr_number_if_reachable(repo: str, branch: str) -> BranchPR:
    """The branch's open PR when GitHub will say, an empty answer when it will not.

    Deliberately *not* folded into ``resolve_local``: that rung promises no
    network at all, and ``pr status`` is built on the promise — it renders a
    dashboard from ``state.json`` and the worktree, and making every invocation
    wait on ``gh`` to find a PR it does not display would be a plain regression.
    So the lookup is opt-in, and the caller that wants it says so.

    ``claude-review --self`` is that caller, and it wants both halves. The
    number fetches the reply threads that keep a re-review from repeating
    findings already answered there; the base is what a stacked branch's diff
    is measured against, and without it the review covers the parent's commits
    as well as its own. Both come off one call — see :class:`BranchPR`.

    Best-effort by construction — ``gh_client.out`` returns "" for a failed
    call, so an unreachable or exhausted API is indistinguishable here from a
    branch with no PR, and both give an empty ``BranchPR``. Callers must read
    that as "no PR known", never as "no PR exists".
    """
    if not branch:
        return BranchPR()
    return _pr_from_branch(repo, branch)


def _target_identity(cwd: str | None) -> pr_target.RepoIdentity:
    """Every name for the target repo from one read of ``origin``, or exit 1.

    Both rungs use it. ``resolve_local`` shows the repo *and* keys the target on
    it; ``resolve`` takes the key and the host while naming the repo through
    ``detect_repo``, which can consult ``gh``. One read is what makes the names
    provably the same repo, and on the shallow rung it is also the only
    subprocess spent on naming.

    Fatal rather than falling back, on both rungs and for the same reason: a run
    keys its state and lock on the origin, so a checkout without one has no
    target to hold.
    """
    identity = pr_target.repo_identity_from_origin(cwd)
    if identity:
        return identity
    _exit_without_an_origin()


def _exit_without_an_origin() -> NoReturn:
    """Fail the run the same way whichever name the caller was asking for."""
    log.error(
        "Cannot read the origin remote — pr keys a run's state and lock on "
        "(origin repo, branch)"
    )
    sys.exit(1)


def head_sha(cwd: str | None = None) -> str:
    """Current HEAD sha of the worktree at *cwd*, or "" if it can't be read.

    Use this when the worktree may have changed since ``resolve()`` — checking
    out a PR or branch can move HEAD after the context was captured.
    """
    return _head_sha(cwd)


def _redirect_to_branch_worktree(
    branch: str, effective_cwd: str,
) -> Path | None:
    """If CWD's branch differs from the target, find the target's worktree."""
    current = git_topology.current_branch_quiet(effective_cwd)
    if current is None or current == branch:
        return None
    return git_topology.find_worktree_by_branch(branch, effective_cwd)


def _resolve_worktree(
    cwd: str | None,
    *,
    pr: str | None,
    branch: str | None,
    create_missing: bool = True,
) -> tuple[Path | None, str | None]:
    """Resolve worktree root, handling bare repos transparently.

    ``create_missing`` is the bare-repo escape hatch: with it False a bare repo
    hands back only worktrees that already exist. Defaulted True and left unset
    by ``resolve`` so that the deep rung keeps creating them.
    """
    toplevel = _git_toplevel(cwd)
    if toplevel is None:
        return _resolve_non_worktree(
            cwd, pr=pr, branch=branch, create_missing=create_missing,
        )

    if branch:
        wt = _redirect_to_branch_worktree(branch, cwd or str(toplevel))
        if wt:
            return wt, str(wt)
    return toplevel, cwd


def _resolve_non_worktree(
    cwd: str | None,
    *,
    pr: str | None,
    branch: str | None,
    create_missing: bool = True,
) -> tuple[Path | None, str | None]:
    """Handle bare repos and non-git directories."""
    if git_topology.is_bare_repo(cwd):
        return _resolve_bare(
            cwd, pr=pr, branch=branch, create_missing=create_missing,
        )

    if not pr and not branch:
        log.error("Not in a git repository")
        sys.exit(1)
    return None, cwd


def _resolve_bare(
    cwd: str | None,
    *,
    pr: str | None,
    branch: str | None,
    create_missing: bool = True,
) -> tuple[Path | None, str | None]:
    """Resolve worktree from a bare repo."""
    wt = (git_topology.resolve_bare_repo_worktree(cwd, branch) if create_missing
          else git_topology.find_bare_repo_worktree(cwd, branch))
    if wt:
        return wt, str(wt)
    if not pr and not branch:
        log.error("Bare repository — pass --branch or --repo-dir")
        sys.exit(1)
    return None, cwd


def detect_repo(cwd: str | None = None) -> str:
    """Detect ``owner/repo``, from ``origin`` where it can and ``gh`` otherwise.

    Single owner for repo detection: the review and comments scripts call
    through here rather than running their own ``gh repo view``.

    ``origin`` is asked first because this is the one call every REMOTE command
    makes before doing anything else, and it was a GraphQL call — ``gh repo
    view`` is GraphQL under the hood. An exhausted GraphQL budget therefore
    failed *every* ``pr`` subcommand at the first step, to learn a string the
    git remote already spells. ``resolve_local`` has always read it from there.

    ``gh`` remains the fallback rather than being dropped, because the origin
    parse answers for github.com and not for every remote: ``RepoIdentity``
    folds case and drops the host, so a GHES or otherwise non-github remote can
    disagree with what the API would call the same repo. Exit 1 still belongs to
    the case where neither can name it — callers downstream treat the repo as
    known.
    """
    identity = pr_target.repo_identity_from_origin(cwd)
    if identity:
        return identity.label

    r = gh_client.run("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner", cwd=cwd)
    slug = r.stdout.strip()
    if not r.ok or not slug:
        log.error(failure_message("Cannot determine repository via `gh repo view`", r))
        sys.exit(1)
    return slug


def _git_toplevel(cwd: str | None = None) -> Path | None:
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, cwd=cwd, timeout=timeouts.LOCAL,
    )
    if r.returncode != 0:
        return None
    return Path(r.stdout.strip())


def _head_sha(cwd: str | None = None) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, cwd=cwd, timeout=timeouts.LOCAL,
    )
    return r.stdout.strip()


def _parse_pr_input(pr_input: str) -> int:
    """Extract PR number from URL or raw number."""
    m = _PR_URL_RE.search(pr_input)
    if m:
        return int(m.group(1))
    if _PR_NUMBER_RE.match(pr_input):
        return int(pr_input)
    raise ValueError(
        f"Cannot parse PR number from {pr_input!r} — expected a number or GitHub PR URL"
    )


def _as_pr_number(said: str) -> int | None:
    """A PR number gh printed, or None when it printed anything else.

    An empty answer is the routine one: a branch with no PR yet 404s, and
    `gh_client` returns that on the first attempt rather than retrying a 4xx.
    """
    try:
        return int(said.strip())
    except ValueError:
        return None


def _pr_from_current(cwd: str | None = None) -> BranchPR:
    data = gh_client.pr_view("", "number", "baseRefName", cwd=cwd)
    return BranchPR(
        number=_as_pr_number(str(data.get("number", ""))),
        base=data.get("baseRefName") or "",
    )


def _pr_from_branch(repo: str, branch: str) -> BranchPR:
    """The open PR for *branch*, both fields off one call.

    The list is read as JSON rather than flattened by a `--jq` interpolation:
    `.[0] | "\\(.number) \\(.baseRefName)"` against an empty list prints the
    literal ``"null null"`` and exits 0, which is two fields and passes any
    arity check. The base then reads as the branch name "null", every range
    resolves to nothing, and a branch with no PR yet — the ordinary case for
    `--self` — is reviewed against a ref that does not exist.
    """
    found = gh_client.json_out(
        "pr", "list", "--repo", repo, "--head", branch,
        "--json", "number,baseRefName", default=[],
    )
    if not isinstance(found, list) or not found:
        return BranchPR()
    first = found[0]
    if not isinstance(first, dict):
        return BranchPR()
    # Both forms coerce a missing/null field to the same falsy default:
    # `_as_pr_number` turns `int("")` into `None`, and `or ""` turns `None`
    # into `""` directly — they just take different routes to get there.
    return BranchPR(
        number=_as_pr_number(str(first.get("number", ""))),
        base=first.get("baseRefName") or "",
    )


def _pr_head(repo: str, pr_number: int) -> PRHead:
    """The PR's head branch, head SHA and base branch, in one API call."""
    r = gh_client.run(
        "pr", "view", str(pr_number), "--repo", repo,
        "--json", "headRefName,headRefOid,baseRefName",
        "-q", '.headRefName + " " + .headRefOid + " " + .baseRefName',
    )
    parts = r.stdout.split()
    # The base is the one field a partial answer may lack without the read
    # having failed: `resolved` gates on the head, which is what the caller
    # cannot continue without, and an empty base falls through the same ladder
    # as a `gh` that could not be reached at all.
    if not r.ok or len(parts) < 2:
        return PRHead(reason=failure_message(
            f"`gh pr view` could not read the head of {repo}#{pr_number}", r))
    return PRHead(
        branch=parts[0], sha=parts[1], base=parts[2] if len(parts) > 2 else "",
    )


