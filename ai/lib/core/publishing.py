"""The gate every outward-facing write passes through.

A PR reply, a summary comment, a tracking issue — each one is visible to other
people the moment it lands, and a wrong one has to be retracted in front of the
reviewer. So the default is to draft: callers print what they would have sent and
report failure, and nothing leaves the machine until the entrypoint opts in.

One flag owns this for the whole process. Modules that write externally
(`pr.comments`, `review.issue`) ask here rather than carrying their own switch.

A hold overrides it. Some things a run learns mid-way — an unanswered question
about whether the work should exist at all — mean nothing more should leave the
machine, whatever the entrypoint was told. `hold` closes the gate for good, so
the two only ever compose in the safe direction.

What that means at the CLI: `pr comments` writes nothing outward unless you
pass `--post`. Replies, the fix summary, thread resolutions, deferral tracking
issues, the PR description, and the push are all printed to stderr as drafts
instead, prefixed `DRAFT (not published)`. Code fixes and the commit are
unaffected: they are local and undoable, and they are what makes the work
reviewable at all. The gate covers what leaves the machine.

`pr ci --fix` and `pr review --fix` answer to the same flag and mean the same
thing by it. Both commit what their agent fixed and both draft the push without
it, so `--post` reads as "publish what this run produces" wherever it appears
next to a fix pass — as against `pr review --post` on its own, which publishes
the review already on disk. The review fix pass runs inside
`review-orchestrate`, a subprocess spawned before any posting decision would
otherwise be made, so `claude-review` forwards the flag to it rather than
opening a gate the pass would never see.

A hand-written `pr comments --reply <id> --body-file <path>` is no exception: it
drafts the body and reports the draft, and only `--post` sends it.

Some comments are answered by rewriting the PR description rather than the code.
That is a GitHub write like any other, so the fix agent does not make it: it is
barred from running `gh` at all, and instead writes the replacement description
to `ignore/pr-comments/pr-description.md` in the worktree. The fix pass sends it
through the same gated client the replies use, which means a run without
`--post` records the intended edit and performs none. The undelivered
description is owed in `pr status` alongside the replies
(`⚠ closeout owed: PR description`) and `--finish --post` delivers it.

The default is draft because a review reply is public the moment it lands: an
incorrect claim has to be retracted in front of the reviewer, and a wrong
deferral issue has to be closed. Reading the drafts first costs one command:

```bash
pr comments --fix              # triage, fix, commit — drafts the push and replies
pr comments --finish --post    # publish once the drafts read correctly
```

A draft run leaves state untouched, so nothing is recorded as posted and a later
`--post` run picks up the same queue.

Filing the deferral tracking issue is the one thing `--post` may stop to ask
about. Nothing assumes a tracker: if `issue_tracker.provider` is unset for the
repo, a `--post` run asks where the repo files issues, then whether to record
the answer for this repo or for all of them. A repo-scoped answer is written to
`.workbench.yml` at the repo root — commit it and nobody is asked again. A
machine-wide answer goes to `config.yml` under the config root.

The question is only ever asked when it can be answered and the answer would
matter. A draft run does not ask, because it files nothing either way. A run
with no terminal at all — CI, or anything else detached from one — reports the
key to set instead of asking. A piped stdin is not that: the question goes to
the terminal the command was started from, so a `--post` run piped into `tee`
still asks. Either way an unanswered question files nothing: no tracking issue
is created and the deferral replies that would link to it are not sent, rather
than an issue being filed to a tracker nobody named.
"""

# doc-group: publishing

from __future__ import annotations

from core import log

_enabled = False
_held = ""


def enable() -> None:
    """Let external writes through for the rest of the process."""
    global _enabled
    _enabled = True


def hold(reason: str) -> None:
    """Close the gate for the rest of the process, whatever `--post` asked for.

    Monotonic: the first reason sticks and nothing reopens the gate. What
    justifies a hold is a question no later stage of the same run can answer,
    so a run that reopened its own gate would be answering it itself.
    """
    global _held
    if _held:
        return
    _held = reason
    log.info(f"Publishing held — {reason}. Nothing further leaves the machine.")


def held() -> str:
    """Why the gate is being held shut, or empty if it is not."""
    return _held


def enabled() -> bool:
    """Whether writes reach the outside world.

    Callers use this to keep their logging honest: a draft is not a failure,
    so error paths must not fire when the gate is closed.
    """
    return _enabled and not _held


def draft(action: str, body: str = "") -> None:
    """Record what would have been written, to stderr."""
    log.info(f"DRAFT (not published) — {action}")
    for line in body.splitlines():
        log.dim(line)
