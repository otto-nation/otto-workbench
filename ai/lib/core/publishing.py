"""The gate every outward-facing write passes through.

A PR reply, a summary comment, a tracking issue — each one is visible to other
people the moment it lands, and a wrong one has to be retracted in front of the
reviewer. So the default is to draft: callers print what they would have sent and
report failure, and nothing leaves the machine until the entrypoint opts in.

`run` owns this for one invocation. Modules that write externally
(`pr.comments`, `review.issue`) ask here rather than carrying their own switch.
Nested `run` calls save and restore, so an inner command cannot inherit an
outer gate it did not open, and cannot close one it did not own.

`pr fix` runs a review, a CI pass and a describe pass in one process, so the
dispatch seam wraps each handler in `scope()` and whatever that handler opened
closes again on the way out — what one pass was told to publish is not an
authorisation for the next.

A hold overrides it. Some things a run learns mid-way — an unanswered question
about whether the work should exist at all — mean nothing more should leave the
machine, whatever the entrypoint was told. `hold` closes the gate for the rest
of that run, so the two only ever compose in the safe direction at both scopes.
The next `run` starts clean: both the flag and the hold reset, or a hold would
outrank a `--post` nobody in that invocation asked to refuse.

What that means at the CLI: `--post` is the gate, not a phase. The phase flags
(`--triage`, `--fix`, `--finish`, `--reply`, `--settle`) choose which work the
run does; `--post` decides whether that work leaves the machine. Every phase
drafts to stderr and publishes nothing without it, so `--post` neither implies
a phase nor is implied by one. `--finish --post` is therefore not saying the
same thing twice. The same `--post` gates `pr review` and `--reply`; it is one
switch for the whole process, not a `comments` flag.

`pr comments` writes nothing outward unless you pass `--post`. Replies, the fix
summary, thread resolutions, deferral tracking issues, the PR description, and
the push are all printed to stderr as drafts instead, prefixed `DRAFT (not
published)`. Code fixes and the commit are unaffected: they are local and
undoable, and they are what makes the work reviewable at all. The gate covers
what leaves the machine. A held push prints the command that would send it —
`git.land` owns the commit (ungated) and the push (gated), and
`push.resume_command` renders the one thing to run.

`pr ci --fix` and `pr review --fix` answer to the same flag and mean the same
thing by it. Both commit what their agent fixed and both draft the push without
it, so `--post` reads as "publish what this run produces" wherever it appears
next to a fix pass — as against `pr review --post` on its own, which publishes
the review already on disk. The review fix pass runs inside
`review-orchestrate`, which is reached before any posting decision would
otherwise be made, so `review` forwards the flag to it rather than
opening a gate the pass would never see. That forwarding predates in-process
dispatch and survives it: the flag is how the pass learns, and `scope()` is
what keeps the answer from outliving the run.

`pr ci --fix`'s rebase-if-behind is in-process too (`cli.ci_check._rebase_if_behind`),
so this run's gate is the one the rebase's push asks. A draft run rebases locally
and drafts the force-push.

A hand-written `pr comments --reply <id> --body-file <path>` is no exception: it
drafts the body and reports the draft, and only `--post` sends it.

Some comments are answered by rewriting the PR description rather than the code.
That is a GitHub write like any other, so the fix agent does not make it: it is
barred from running `gh` at all, and instead writes the replacement description
to `pr-description.md` in the pass's artifact directory. The fix pass sends it
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
about. Nothing assumes a tracker: if `issues.provider` is unset for the
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

import contextlib
import importlib
from collections.abc import Iterator
from contextlib import contextmanager

import core.log

_enabled = False
_held = ""


@contextmanager
def run(*, post: bool = False):
    """Scope the gate to this invocation.

    `post=True` is `--post`. Both `_enabled` and `_held` are restored on exit,
    including when this `run` is nested inside another: an inner reset must
    not close the outer invocation's gate, and an inner `--post` must not
    leave the outer one open.
    """
    global _enabled, _held
    prev_e, prev_h = _enabled, _held
    _enabled, _held = post, ""
    try:
        yield
    finally:
        _enabled, _held = prev_e, prev_h


def enable() -> None:
    """Let external writes through for the rest of the current run, or the
    enclosing `scope()` if one is open.

    An entry point calls this when its own flags say the run may publish. What
    bounds it is the `run` or `scope()` its caller opened, not this call.
    """
    global _enabled
    _enabled = True


@contextlib.contextmanager
def scope() -> Iterator[None]:
    """Bound whatever the code inside opens to the run that opened it.

    `pr fix` runs a review, a CI pass and a describe pass in one process. A
    gate opened by the first is a published artifact nobody authorised in the
    third. Until in-process dispatch, the subprocess boundary was doing this
    scoping by accident, and there was nothing that could close the gate at
    all.

    Opened by the **dispatch seam** rather than by each entry point, which is
    what makes it an invariant instead of a convention: a delegate that calls
    `enable()` and forgets to bound it is still bounded, and a new one cannot
    reintroduce the leak by omission. The five entry points that call
    `enable()` are unchanged.

    Restores the previous value rather than clearing, so a run nested in an
    already-open one leaves the outer gate as it found it.

    A `hold` is deliberately **not** restored. It records something a run
    learned that means nothing more should leave the machine, and that
    conclusion outlives the run that reached it — an outer pass must not
    resume publishing because an inner one finished. `hold` is monotonic
    within a process for that reason, and this is the same rule one scope up.
    """
    global _enabled
    previous = _enabled
    try:
        yield
    finally:
        _enabled = previous


def hold(reason: str) -> None:
    """Close the gate for the rest of the run, whatever `--post` asked for.

    Monotonic within a run: the first reason sticks and nothing reopens the
    gate. What justifies a hold is a question no later stage of the same run
    can answer, so a run that reopened its own gate would be answering it
    itself. `scope()` does not restore it either, for the same reason one
    scope up. The next `run` starts with a clear hold; leaking one would
    refuse a `--post` that invocation never saw.
    """
    global _held
    if _held:
        return
    _held = reason
    core.log.info(f"Publishing held — {reason}. Nothing further leaves the machine.")


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
    core.log.info(f"DRAFT (not published) — {action}")
    for line in body.splitlines():
        core.log.dim(line)


def call_entry_point(handler: str, argv: list[str], **kwargs) -> int:
    """Run an entry point's `main` in this process, and return its exit code.

    The in-process counterpart of a spawn, and it exists to preserve the two
    properties the process boundary was providing for free. Both were
    load-bearing and neither had another owner.

    **However the callee *exits* — cleanly or via `sys.exit` — the caller gets
    an int.** A child that called `sys.exit` was still just a returncode to its
    parent. In one process that same call is a `SystemExit` unwinding through
    the caller: `pr fix`'s review pass exiting 0 because the operator declined
    a prompt would take the CI and describe passes with it and report success.
    `SystemExit` is caught and converted by CPython's own rule — None is 0, an
    int is itself, anything else prints and is 1. There are about thirty
    `sys.exit` sites under `review/` and `pr/`, several of them legitimate for
    a library; one guarantee here beats thirty conversions that a thirty-first
    would undo. An arbitrary uncaught exception is not `sys.exit` and is not
    converted: it propagates, same as it would with no seam here at all.

    **What the callee publishes is scoped to the callee**, via `scope()`.

    `KeyboardInterrupt` is deliberately not caught. It belongs to the entry
    point's signal handler, which reports the interrupt once for the whole
    invocation.

    Here rather than in `core.proc`, which is where a reader looks for "run a
    thing and get its returncode": `proc` is stdlib-only on purpose, and
    `test_proc_imports_nothing_from_ai_lib_but_timeouts` holds it to that.
    Half of this function is the gate, which is this module's subject, so the
    gate is what it was folded into rather than the other way round. Layer 1
    either way, which is what `review.invoke` needs — it cannot import the
    `cli` package that `pr` dispatches through.
    """
    module_name, attr = handler.split(":", 1)
    main = getattr(importlib.import_module(module_name), attr)
    with scope():
        try:
            return exit_code_of(main(argv, **kwargs))
        except SystemExit as exc:
            return exit_code_of(exc.code)


def exit_code_of(value: object) -> int:
    """Turn an entry point's return value (or `SystemExit.code`) into a returncode.

    `None` means success and returns 0. An `int` is already a returncode and
    is returned as-is. Anything else is logged to stderr and reported as a
    failing status (1) — the same rule CPython applies to the argument of
    `sys.exit`, so a caller of this function sees the same behaviour it
    would have gotten from an uncaught `SystemExit`.
    """
    if value is None:
        return 0
    if isinstance(value, int):
        # `bool` is an `int` subclass, so `sys.exit(True)` and `sys.exit(False)`
        # land here too and return 1 and 0 respectively — the same values
        # CPython itself would use, so this is intentional rather than a gap.
        return value
    core.log.error(str(value))
    return 1
