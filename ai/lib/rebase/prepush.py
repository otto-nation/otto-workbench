"""Repairing the pre-push checks a rebased branch failed, then landing the fix.

The second rung of ``land``'s recovery ladder: the hook rewrote what it could,
pushed again, and still reported failures. What is left is a complaint an agent
can act on — except for generated files, which are rebuilt rather than edited,
because prompting one costs a whole call to produce what the generator emits in
milliseconds.

``fix.engine`` runs the pass and this says what a rebase hands it, the way
``fix.ci`` does for CI. The adapter lives here rather than in ``fix/`` because
it reads ``rebase.conflicts`` and ``rebase.repo_regen``, and an adapter's home
is the lowest layer *its own* imports permit — ``fix.engine.run()`` takes the
adapter as an argument, so nothing requires the two to sit together.

**Two landings, and why they are not one.** An item is what the agent is
*shown*, so a generated file cannot be one: the backend edits in place, and
handing it a protobuf descriptor is the single outcome this module exists to
prevent. A repair that is entirely a rebuild therefore has no items, and the
engine correctly runs nothing — but the push was refused by a drift check and
the rebuild *is* the repair, so withholding it leaves the branch unpushable and
the operator holding a rebuilt tree with no commit carrying it. `fix_push_failures`
lands that case itself. When there is also editable work, there is no second
commit: the engine's landing is whole-tree, so the rebuilt artifacts ride in the
agent's commit. That is deliberate rather than incidental — a pre-push hook
validates the worktree and not the commits under it, so splitting the rebuild
into a commit of its own would push a HEAD the green run never saw.

The rebuild runs before the engine either way, so no turn of the agent's budget
is spent on a file a generator owns and the tree it reads already holds correct
artifacts.
"""

# doc-group: platform

from __future__ import annotations

from pathlib import Path

from agent import backend as ai_backend
from core import log
from core.phases import Phase
from core.trail import Trail, terr, tinfo
from fix import engine as fix_engine
from fix import types as fix_types
from git import land
from git import regenerate as regen
from pr.fix import ItemOutcome

from . import conflicts as rebase_conflicts
from . import repo_regen
from . import types as rebase_types

GeneratedFix = rebase_types.GeneratedFix
RegenQueue = regen.RegenQueue

FORCE_PUSH_ARGS = rebase_types.FORCE_PUSH_ARGS
REGEN_MESSAGE = rebase_types.REGEN_MESSAGE


# One budget for every chunk of context pasted into a fix prompt. It is
# truncated on a plain slice, so a hunk can be cut mid-line; these prompts are
# best-effort and a partial tail still reads fine.
FIX_ERROR_MAX_CHARS = 4000
FIX_SUBJECT = "fix: resolve pre-push check failures after rebase"

# The action every entry this module writes is filed under. Unchanged from the
# hand-rolled loop this replaces: it is what `otto-log query` is searched on,
# and a rename would silently empty out the searches already written against it.
TRAIL_ACTION = "fix_push_failures"


def files_named_in(check_output: str, candidates: list[str]) -> list[str]:
    """Pick the candidates the failing check actually complained about.

    A long rebase resolves conflicts in dozens of files while the check that
    then fails names two of them. Handing the agent every resolved file spends
    budget per entry and gives it edit access to a file the check had no
    complaint about — which is how a file the branch never touched ends up
    rewritten.

    Matching on the bare name as well as the path catches checks that report a
    basename only. That can pull in a same-named file from another directory;
    one extra file is the acceptable side of this trade.
    """
    return [
        path for path in candidates
        if path in check_output or Path(path).name in check_output
    ]


def generated_among(targets: list[str], cwd: str) -> list[str]:
    """The targets that are generated artifacts rather than hand-written files.

    Classification only — the rebuild is a separate act, run once by the caller.
    Keeping this pure is what lets the adapter be constructed in a test without
    a build running as a side effect of asking it what its items are.
    """
    return [
        f for f in targets
        if rebase_conflicts.is_generated_file(f, Path(cwd) / f, cwd) is not None
    ]


def regenerate(
    generated: list[str], cwd: str, *, trail: Trail | None = None,
) -> GeneratedFix:
    """Rebuild the generated files a failing check named, sparing them the AI.

    A generated file's content is a function of its sources, so a check failing
    on one means the sources moved and the artifact did not — a rebuild is the
    only correct repair. Hand-editing one is wrong even when the edit parses:
    the next regeneration overwrites it, and the drift check that rejected the
    push is comparing against generator output, not against plausibility.

    It is also ruinous to attempt. These files run to serialized descriptor
    blobs and hash manifests, and prompting one costs an entire call to produce
    something the generator emits in milliseconds.

    Every generated file is excluded whether or not a rebuild was available,
    because "we cannot rebuild it" is not a reason to edit it by hand.
    """
    queue = RegenQueue()
    stale: list[str] = []

    for filepath in generated:
        if not repo_regen.queue_repo_regeneration(filepath, cwd, queue):
            stale.append(filepath)

    if stale:
        log.warn(f"No regeneration command for {len(stale)} generated file(s)")
        tinfo(
            trail, TRAIL_ACTION, "generated files with no regeneration command",
            data={"files": stale},
        )

    rebuilt = False
    for job in queue:
        if regen.run_regeneration(job, cwd=cwd, trail=trail):
            rebuilt = True
            continue
        stale.extend(f for f in job.files if f not in stale)

    return GeneratedFix(excluded=list(generated), rebuilt=rebuilt, stale=stale)


def _outcome_line(outcome: ItemOutcome) -> str:
    """One file's verdict, in the agent's own words where it gave any."""
    where = outcome.file or outcome.id
    return f"- {where} — {outcome.reason}" if outcome.reason else f"- {where}"


class PrePushFixAdapter(fix_engine.FixAdapter):
    """A rebase's half of a fix pass: the named files, the commit, the record.

    The items are the files the failing check complained about, minus the
    generated ones — those are rebuilt by the caller before this runs and never
    reach the agent. `editable` is what the pass is actually asked about, and an
    empty one is a run with nothing for an agent to do.

    The check output is handed to the agent whole rather than sliced per file.
    It is one report about one failed run, and the line implicating a file is
    routinely not the line naming it — a build error names the file that failed
    to compile and the cause is the signature that moved in another.
    """

    phase = Phase.PREPUSH_FIX
    title = "Pre-push Fix Tracking"
    action = "fixing pre-push check failures"
    item_noun = "file"

    def __init__(
        self, cwd: str, editable: list[str], check_output: str, *,
        repo: str = "", pr: str = "", branch: str = "",
        trail: Trail | None = None,
    ) -> None:
        self.workdir = Path(cwd)
        self.artifacts = self.workdir / "ignore" / "pr-rebase"
        self.editable = editable
        self.check_output = check_output
        self.repo = repo
        self.pr = pr
        self.branch = branch
        self.trail = trail

    def items(self) -> list[fix_types.FixItem]:
        """One item per file the check named, keyed by the path itself.

        The path is the id because it is the only identifier a check failure
        has — there is no run number, no thread and no finding behind it — and
        because an outcome keyed by path is one a reader can place without the
        tracking file that produced it.
        """
        return [
            fix_types.FixItem(
                id=path, file=path, label="named by the failing check",
                body=f"The pre-push check named `{path}`. Repair it against "
                     "the check output in the prompt.",
            )
            for path in self.editable
        ]

    def template_vars(self) -> dict[str, str]:
        """The check output, which is this domain's whole statement of the work."""
        return {"check_output": self.check_output}

    def landing(self, outcomes: list[ItemOutcome]) -> fix_engine.LandSpec:
        """Commit the whole tree and force-push it.

        Three things this domain needs that a fix pass does not always:

        `args` carries the lease. The branch under this commit was replayed, so
        its push is non-fast-forward and a plain one is rejected.

        `paths` is None on purpose. The backend runs with ``acceptEdits`` and
        ``Bash(*)``, so a repair can land anywhere in the worktree rather than
        only in the files named here — and a narrower commit is how an edit gets
        validated by the retry's hooks and then left out of what is pushed.

        `recover` accounts for an agent that committed its own work. The pass
        then finds nothing to stage, and reporting "nothing needed doing" over a
        real repair is the failure that guards against.

        The subject is static and the body is the outcomes. This branch is
        squash-merged with COMMIT_MESSAGES, so what is written here lands
        verbatim on main — where each file's verdict in the agent's own words
        says more than a generated line describing the diff underneath it.
        """
        fixed = sum(1 for o in outcomes if o.outcome.counts_as_fixed)
        message = FIX_SUBJECT
        if outcomes:
            message += f"\n\n{fixed} fixed, {len(outcomes) - fixed} unresolved"
            message += "\n\n" + "\n".join(_outcome_line(o) for o in outcomes)
        return fix_engine.LandSpec(
            message=message, regen=REGEN_MESSAGE, recover=True,
            args=FORCE_PUSH_ARGS,
        )

    def record(self, run: fix_engine.FixRun) -> None:
        """Report the pass on the trail, which is the only durable place there is.

        Every `Domain` carries a `fix: FixRecord` and this writes none, because
        persisting one needs the `target_dir` that identifies the branch's state
        and nothing on this path has it.

        ceiling: the pass is reported to the trail and not recorded in state.
        `land_rebased` resolves no context, so there is no `target_dir` to save a
        `FixRecord` against. Widen `land_rebased` to take the ctx both its call
        sites already hold if the rebase domain's fix record has to survive the
        run that produced it.
        """
        landed = run.landed
        sha = landed.sha if landed else ""
        # A pass that answered every item and committed nothing is a real
        # outcome, not an absent one — reporting it as a commit would put a
        # fix on the trail that no SHA carries.
        tinfo(
            self.trail, TRAIL_ACTION,
            "committed check-failure fixes" if sha
            else "pre-push fix pass committed nothing",
            data={
                "files": [o.id for o in run.outcomes],
                "fixed": sum(1 for o in run.outcomes if o.outcome.counts_as_fixed),
                "sha": sha,
                "status": str(landed.status) if landed else "",
            },
        )


def fix_push_failures(
    cwd: str, error_output: str, resolved_files: list[str],
    *, trail: Trail | None = None,
) -> land.LandResult | None:
    """Fix pre-push check errors, then land the repair.

    Generated files are rebuilt from their sources; everything else goes to a
    fix pass, which interprets the check output and repairs formatting, build,
    or lint issues. Language-agnostic — the project's own checks are the oracle.

    None when no fix was attempted or nothing came of one — nothing to rebuild
    and no backend, or neither pass produced anything worth committing. The
    caller then reports the push that sent it here, which is still the honest
    answer about the branch.
    """
    truncated = error_output[:FIX_ERROR_MAX_CHARS]
    # A file that conflicted in several replayed commits is listed once per
    # conflict; fixing it once is enough.
    candidates = list(dict.fromkeys(resolved_files))
    # Matched against the truncated output on purpose: it is what the pass shows
    # the agent, so a file named only past the cut has no visible complaint.
    targets = files_named_in(truncated, candidates)
    if not targets:
        # A check can fail without naming a path, and then every resolved file
        # is still a suspect. Recorded because it is the expensive path.
        targets = candidates
        tinfo(
            trail, TRAIL_ACTION, "check output named no resolved file",
            data={"files": candidates},
        )

    generated = regenerate(generated_among(targets, cwd), cwd, trail=trail)
    editable = [f for f in targets if f not in generated.excluded]

    # The engine invokes the backend unconditionally, so the availability check
    # stays here: without it a machine with no backend pays for the tracking
    # file and the batching before failing at the call.
    if not editable or not ai_backend.is_available():
        return _land_rebuild(cwd, generated, trail=trail)

    context = trail.context if trail else {}
    pr = context.get("pr")
    adapter = PrePushFixAdapter(
        cwd, editable, truncated,
        repo=str(context.get("repo") or ""),
        pr=str(pr) if pr else "",
        branch=str(context.get("branch") or ""),
        trail=trail,
    )
    return fix_engine.run(adapter, trail=trail).landed


def _land_rebuild(
    cwd: str, generated: GeneratedFix, *, trail: Trail | None = None,
) -> land.LandResult | None:
    """Commit and force-push a repair that was entirely a regeneration.

    The engine's landing is unreachable here — it lands what an agent produced,
    and there was no agent — but the rebuild still has to reach the remote or
    the branch stays unpushable for the same drift the hook rejected.
    """
    if not generated.rebuilt:
        return None

    landed = land.land(
        cwd, message=REGEN_MESSAGE, gated=True, args=FORCE_PUSH_ARGS, trail=trail,
    )
    if landed.sha:
        tinfo(
            trail, TRAIL_ACTION, "committed regenerated files",
            data={"files": generated.excluded, "sha": landed.sha},
        )
    else:
        terr(
            trail, TRAIL_ACTION, "regenerated files did not commit",
            data={"files": generated.excluded, "status": str(landed.status)},
        )
    return landed
