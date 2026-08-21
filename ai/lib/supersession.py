"""Whether a branch's reason to exist is already gone.

A branch can be rebased over a `main` that has deleted the code it was
fixing, and the reviewer's "this does not exist any more" is one thread among
ten. None of that needs an AI call to notice — the skew is in the commit dates,
the re-addition is in the diff, and the PR that removed it is one search away.

This module answers the question; it does not decide what to do about it. The
two are separated because the callers legitimately differ. `pr comments` has
already spent its money by the time it publishes, so a positive verdict holds
the publishing. `pr review` spends the largest budget of any command in the
repo, so a positive verdict refuses before the spend rather than after it. One
detection, two policies, each stated where the cost is.

Distinct from `pr rebase`'s already-landed check, which asks whether the work
has *landed* rather than whether it has been *superseded*. Work can land
without the branch being superseded, and a branch can be superseded without its
commits having landed anywhere — someone solved the problem differently. They
stay separate: two of the landed check's three signals are local-only, and this
one makes a network call that a rebase should not have to pay for.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import git_client
import log
import pr_context
import pr_state
import timeouts
from pr_state import SupersessionKind, SupersessionSignal
from trail import Trail

# Author-vs-committer drift on the first commit, in days, before the branch is
# plainly sitting on a base that moved under it. Days, not hours: a same-day
# rebase is just a push after `wt merge`.
_REBASE_SKEW_DAYS = 7
# This is a preflight, not an investigation. Whether the branch re-adds anything
# the default branch removed is answered as well by the first few symbols as by
# all of them, and each one costs two git calls.
_PREFLIGHT_SYMBOL_LIMIT = 10
# ...and each flagged one costs a network round trip on top.
_PREFLIGHT_SEARCH_LIMIT = 2
# A definition line, in the languages this repo and its neighbours are written
# in. Deliberately shallow: a missed symbol costs one unfired signal, and a
# wrong one costs a search that finds nothing.
_SYMBOL_DEF_RE = re.compile(
    r"^\+\s*(?:export\s+|pub\s+|public\s+|private\s+)?(?:async\s+)?"
    r"(?:func|def|class|type|interface|struct|fn)\s+"
    r"(?:\([^)]*\)\s*)?"
    r"([A-Za-z_]\w{3,})",
)


# The exit code a command uses to refuse a branch it judges superseded, and the
# flag that overrides the refusal. Both are the ones `pr rebase` already uses
# for its already-landed refusal: the two checks answer different questions, but
# a caller reading the exit code only needs to know "this branch was refused",
# and one code for that beats two it has to tell apart.
EXIT_SUPERSEDED = 4
OVERRIDE_FLAG = "--force"


@dataclass(frozen=True)
class AddedSymbol:
    """A definition the branch adds, and the file it adds it in."""

    name: str
    path: str


@dataclass(frozen=True)
class Verdict:
    """What the checks found, and the SHAs they found it against.

    The SHAs travel with the signals rather than being re-read by whoever wants
    to cache the answer: a verdict computed at one HEAD and stored against
    another is exactly the staleness the cache key exists to prevent.
    """

    signals: list[SupersessionSignal] = field(default_factory=list)
    head_sha: str = ""
    base_sha: str = ""

    @property
    def holding(self) -> list[SupersessionSignal]:
        """The signals that are evidence, as opposed to context."""
        return [s for s in self.signals if s.holds]

    @property
    def superseded(self) -> bool:
        """Whether anything found rises to evidence the branch is superseded.

        This is the predicate every caller branches on. Signals that only
        provide context — a rebase over a moved base, which every long-lived
        branch has — are printed but do not make this true.
        """
        return bool(self.holding)


def _rev(wt_path: Path, ref: str) -> str:
    """`ref` resolved to a SHA, or empty when it cannot be.

    Empty is a usable answer: it makes `SupersessionDomain.matches` fail, so a
    verdict computed against a ref that would not resolve is never reused.
    """
    return git_client.out("rev-parse", ref, cwd=wt_path)


def _rebase_skew_days(wt_path: Path, base: str) -> int:
    """Days between when the branch's first commit was written and committed.

    Non-zero means the commit was replayed — a rebase, a cherry-pick — and a
    large value means it was replayed onto a base that had moved a long way.
    Unreadable output reads as no skew: this is a hint, and a hint that cannot
    be computed is not a finding.
    """
    out = git_client.out(
        "log", "--reverse", "--format=%at %ct", f"{base}..HEAD", cwd=wt_path)
    if not out:
        return 0
    parts = out.splitlines()[0].split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return 0
    return max(0, (int(parts[1]) - int(parts[0])) // 86400)


def _branch_added_symbols(wt_path: Path, base: str) -> list[AddedSymbol]:
    """Definitions this branch adds.

    Three-dot: what the branch adds relative to the merge base. A brand-new
    definition and one re-added over a deletion are indistinguishable here —
    `_removed_from_base` is what tells them apart.
    """
    found: dict[str, str] = {}
    path = ""
    for line in git_client.lines("diff", f"{base}...HEAD", cwd=wt_path):
        if line.startswith("+++ b/"):
            path = line[len("+++ b/"):]
            continue
        match = _SYMBOL_DEF_RE.match(line)
        if match and path and len(found) < _PREFLIGHT_SYMBOL_LIMIT:
            found.setdefault(match.group(1), path)
    return [AddedSymbol(name, path) for name, path in found.items()]


def _removed_from_base(wt_path: Path, base: str, symbol: str, path: str) -> str:
    """The base commit that last touched `symbol`, if the base no longer has it.

    Absent from the base plus a history that once contained it is the whole
    signal: a symbol the base never had is simply new. Pinned to the file the
    branch adds it in, which keeps the pickaxe off the full history.
    """
    if git_client.ok("grep", "-q", "-F", symbol, base, cwd=wt_path):
        return ""
    found = git_client.lines(
        "log", "--format=%h", "--max-count=1", f"-S{symbol}", base, "--", path,
        cwd=wt_path)
    return found[0] if found else ""


def _superseding_prs(repo: str, symbols: list[str]) -> list[SupersessionSignal]:
    """Merged PRs that mention a re-added symbol.

    The PR that removed it is the thing worth reading before working on this
    branch at all, and its title usually says so.
    """
    found = []
    for symbol in symbols[:_PREFLIGHT_SEARCH_LIMIT]:
        title = _merged_pr_mentioning(repo, symbol)
        if title:
            found.append(SupersessionSignal(
                SupersessionKind.SUPERSEDING_PR,
                f"a merged PR mentioning `{symbol}`: {title}",
            ))
    return found


def _merged_pr_mentioning(repo: str, symbol: str) -> str:
    """The first merged PR mentioning `symbol`, as `#N title`, or empty.

    A supersession signal is advisory — it tells a reader what to look at
    before starting, and every caller treats its absence as "nothing found".
    A search that outruns its bound therefore degrades to silence rather than
    raising through a preflight the reader only asked for a hint from.
    """
    try:
        result = subprocess.run(
            ["gh", "api", f"search/issues?q=repo:{repo}+{symbol}+is:merged",
             "--jq", '.items[0] // empty | "#\\(.number) \\(.title)"'],
            capture_output=True, text=True, timeout=timeouts.NETWORK,
        )
    except subprocess.TimeoutExpired:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def detect(
    wt_path: Path, repo: str, base: str = "", trail: Trail | None = None,
) -> Verdict:
    """Run the cheap checks against the branch checked out at `wt_path`.

    `base` is accepted rather than always resolved so a caller that already
    knows the default branch does not pay for it twice; empty means resolve it
    here.
    """
    base = base or f"origin/{pr_context.default_branch(wt_path)}"
    signals: list[SupersessionSignal] = []

    skew = _rebase_skew_days(wt_path, base)
    if skew >= _REBASE_SKEW_DAYS:
        signals.append(SupersessionSignal(
            SupersessionKind.REBASE_SKEW,
            f"the first commit was written {skew} day(s) before it was committed "
            f"— this branch has been replayed onto a base that moved",
            holds=False,
        ))

    readded = []
    for symbol in _branch_added_symbols(wt_path, base):
        removed_in = _removed_from_base(wt_path, base, symbol.name, symbol.path)
        if not removed_in:
            continue
        readded.append(symbol.name)
        signals.append(SupersessionSignal(
            SupersessionKind.READDS_REMOVED_SYMBOL,
            f"`{symbol.name}` is added by this branch but absent from {base}, "
            f"which last touched it in {removed_in} ({symbol.path})",
        ))

    signals.extend(_superseding_prs(repo, readded))

    if trail and signals:
        trail.info("supersession_detect", f"{len(signals)} signal(s)",
                   data={"signals": [s.kind for s in signals]})
    return Verdict(
        signals=signals,
        head_sha=_rev(wt_path, "HEAD"),
        base_sha=_rev(wt_path, base),
    )


def detect_cached(
    wt_path: Path,
    repo: str,
    target_dir: Path | None,
    base: str = "",
    trail: Trail | None = None,
) -> Verdict:
    """`detect`, reusing a stored verdict computed against the same commits.

    The cost this avoids is the `gh api search/issues` call per re-added symbol.
    `pr` runs its delegates as separate subprocesses, so a process-global cache
    would never cross from `pr review` to `pr comments`; the state file is the
    only place a verdict survives between them.

    Two cases compute without storing, for the same reason from two directions:
    no `target_dir` at all (the caller has not resolved a run target), and a
    `target_dir` with no state file in it yet. Writing either would mean
    inventing a `PRIdentity` this module has no business deciding, and the
    command that owns the state writes it moments later anyway.
    """
    base = base or f"origin/{pr_context.default_branch(wt_path)}"
    head_sha, base_sha = _rev(wt_path, "HEAD"), _rev(wt_path, base)

    state = pr_state.load_state(target_dir) if target_dir else None
    if state and state.supersession.matches(head_sha, base_sha):
        if trail:
            trail.info("supersession_cache_hit", "reused cached verdict",
                       data={"head_sha": head_sha, "base_sha": base_sha})
        return Verdict(
            signals=list(state.supersession.signals),
            head_sha=head_sha,
            base_sha=base_sha,
        )

    verdict = detect(wt_path, repo, base, trail)

    if state:
        pr_state.apply(state, pr_state.SupersessionDomain(
            updated_at=pr_state.now_iso(),
            head_sha=verdict.head_sha,
            base_sha=verdict.base_sha,
            signals=list(verdict.signals),
        ))
        pr_state.save_state(target_dir, state)
    return verdict


def report(verdict: Verdict) -> None:
    """Print what the checks found.

    Every signal is printed with its kind, so the output says which check fired
    rather than leaving the operator to guess why the run went quiet. Shared by
    both callers because what was found does not depend on what is done about
    it.
    """
    if not verdict.signals:
        return
    log.warn("Supersession preflight — this branch may already be superseded:")
    for signal in verdict.signals:
        log.warn(f"  [{signal.kind}] {signal.detail}")
