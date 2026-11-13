"""Prose more than one command contract carries, written once.

`PUSH_WAIT` goes on every contract that pushes a branch, so each one says the
same thing about a push that seems stuck. `QUEUED` goes on every command that
waits for an otto-queue slot before it runs.
"""

# doc-group: cli

PUSH_WAIT = (
    "Slow or hung? A push that prints '⏱ still pushing <branch>' every minute is slow, "
    "not hung: git is waiting on the pre-push hook or the transfer, and the workbench "
    "never kills a push (WORKBENCH_PUSH_HEARTBEAT_SECS sets the interval). This repo's "
    "own hook bounds each captured step (WORKBENCH_PREPUSH_STEP_TIMEOUT) and names the "
    "step it killed. Use --no-verify only for a hook failure already understood — a "
    "flake, or a check the branch did not cause — never because a push is slow."
)

QUEUED = (
    "AI work waits for an otto-queue slot first (exit 75 if none came in time, "
    "130 if cancelled); --no-queue or WORKBENCH_PR_NO_QUEUE=1 skips it."
)
