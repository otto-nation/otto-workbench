"""Taking the run locks a `pr` command declares.

Every `pr` command declares the locks it holds on its `Need`, in
`cli.registry`, as a set of `core.run_lock` scopes; no handler chooses its own.
This module is the one place they are taken for a command:

* `hold_declared` — `pr`'s dispatcher, for the whole dispatch. Globals before
  the context is resolved; target and checkout once it is, before the worktree
  is fetched and before the handler runs.
* `claim_declared` — the entry points that can be run without `pr` (`review`,
  `ci-check`, `review-threads`, `pr-rebase`, `pr-describe`), for the rest of
  the process. Under `pr` the lock is already held and this passes through;
  run directly, it is the lock.

| Command / mode | Holds |
|---|---|
| `batch run`, `batch resume` | global `batch`; the handler then holds every item's target until the run settles |
| `batch resolve` | its run's own lock, in the handler — never the global, so it reaches a live run |
| `batch cancel`, `status`, `next`, `plan` | nothing |
| `rebase`, `push`, `describe`, `fix` | target + checkout |
| `comments` | target + checkout; `--reply` / `--settle` the target only |
| `create` | target + checkout; `--dry-run` nothing |
| `ci` | target; `--fix` adds the checkout |
| `review --self` / `--self --fix` | target + checkout |
| `review` of a PR, `--branch` | target; the checkout once the run knows its tree (`core.run_lock.lock_checkout`) |
| `review --post`, `--repair`, `--summary` | target (also with `--self`) |
| `review --recover` | target (`--self --recover` adds the checkout) |
| `review --list`, `status` | nothing |
| `gc` | target, plus each pruned target's own lock |

A bare `review` on a branch with no PR is not declared as `--self`: the
dispatcher injects `--self` through `_review_argv` once the context is known,
and `_held_scopes` then reads the `--self` need, so it holds target+checkout.
`review <pr>` stays TARGET and takes the checkout later through `lock_checkout`.

`--self --post` / `--summary` / `--repair` (no `--fix`) stay REVIEW_MODE_NEED —
REMOTE, no fetch, TARGET only. Those flags are handler modes, so they do not
lock the checkout.

The table is pinned against the registry by `tests/pr_cli_dispatch_test.py`,
and `bin/local/validate-run-locks` refuses a lock taken anywhere this module
and its short allowlist do not account for.
"""

# doc-group: cli

from collections.abc import Iterable, Sequence

import cli.registry
import core.run_lock
import pr.context
import pr.state


def _label(ctx: pr.context.ResolvedContext) -> str:
    """`repo@branch`, which is how a refusal names a target."""
    return f"{ctx.repo}@{ctx.branch}" if ctx.repo and ctx.branch else ""


def hold_declared(scopes: Iterable[core.run_lock.LockScope],
                  ctx: pr.context.ResolvedContext | None, *, command: str):
    """A context manager holding *scopes* for *ctx*, in the Global → TARGET → CHECKOUT order.

    *ctx* is None for the globals `pr` takes before it resolves anything; a
    TARGET among *scopes* then raises ValueError. The checkout is the context's
    worktree, skipped when it has none. *command* is what a contender's refusal
    names. Raises LockBusy, having released what it took, when any scope is
    held elsewhere.
    """
    if ctx is None:
        return core.run_lock.hold(scopes, command=command, started=pr.state.now_iso())
    # A test, not `or None`: validate-worktree-guards reads the AST, and only a
    # test is visible to it as the deliberate opt-out.
    worktree = ctx.worktree_root if ctx.worktree_root else None
    return core.run_lock.hold(scopes, command=command, started=pr.state.now_iso(),
                              target_dir=ctx.target_dir, worktree=worktree,
                              target_label=_label(ctx))


def claim_declared(name: str, argv: Sequence[str], ctx: pr.context.ResolvedContext, *,
                   command: str) -> None:
    """Hold what command *name* declares for *argv* for the rest of the process, or exit 1.

    For the entry points that can be run without `pr`. The scopes are read off
    the registry entry `pr`'s dispatcher reads, over the delegate's own argv,
    so `ci-check --fix` locks the checkout exactly when `pr ci --fix` does.
    Under `pr` the locks are already held — by this process, or by the parent
    named in `WORKBENCH_RUN_LOCK` — and this passes through.

    *command* is what a contender's refusal names. A command declaring a global
    raises ValueError: a global is held for one dispatch by `pr`, never claimed
    for a process lifetime by a delegate.
    """
    scopes = cli.registry.need_for(cli.registry.COMMANDS[name], argv).locks
    if core.run_lock.globals_in(scopes):
        raise ValueError(f"{name} declares a global lock, which only pr's dispatcher holds")
    if core.run_lock.TARGET not in scopes:
        return
    worktree = (ctx.worktree_root
                if core.run_lock.CHECKOUT in scopes and ctx.worktree_root else None)
    core.run_lock.claim_for_process(ctx.target_dir, command=command,
                                    started=pr.state.now_iso(), worktree=worktree,
                                    target_label=_label(ctx))
