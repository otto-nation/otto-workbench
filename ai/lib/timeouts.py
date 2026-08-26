"""How long a subprocess may run, decided once instead of at every call site.

`ai/` decided this in 22 places and four different ways: constants scoped to one
module, bare literals at the call site, a caller-supplied argument, and — for
most of the git client — nothing at all. The same operation got a different
bound depending on which file it was called from; a single `gh api` round trip
was 30s in `review_github`, 10s in `pr-rebase`, and 10s in `retro-scan`. No
principle separated those numbers. They were what whoever wrote each site
happened to pick.

Leaving the choice with callers is what produced the spread, so the fix is not a
better default for `timeout=` — it is taking the decision away from the call
site. A caller picks a tier that describes its operation; it does not pick a
number.

The tempting axis for those tiers is how long the work takes. The axis that
actually predicts the right answer is **what bounds the cost**:

| Tier | For | Why |
|---|---|---|
| `QUICK` | A `--value-flags` probe, a session hook reading one file | Should answer instantly; a breach is a wedged process, never real work. |
| `LOCAL` | Flat-cost local reads — `rev-parse`, `merge-base`, `log`, `grep`, `diff`, a `yq` parse | Scales with neither history nor tree size in any way that approaches the bound. |
| `NETWORK` | One round trip — a single `gh api` call, a tracker CLI, an HTTP request | Bounded by latency, not payload, so a breach means the far end stopped answering. |
| `TRANSFER` | Data-proportional over a socket — `fetch`, `gh api --paginate` | As large as the history or the result set, but a socket can stall in a way waiting will not fix. |
| `UNBOUNDED` | `worktree add`, `commit`, `push`, `rebase`, `checkout`, `stash`, `add` | A bound would be wrong, not merely large. |

For the first three tiers the cost is the same whatever the repository holds, so
a breach means something is genuinely wrong — a hang, a dead socket, a deadlock —
and a timeout is a hang detector. For the last two the cost is whatever the input
costs, and a breach is indistinguishable from "the repository is large" or "this
repo's pre-commit hook runs a test suite". A fixed timeout there silently
converts a large repo into a broken tool, which is why `UNBOUNDED` exists and is
spelled out rather than omitted.

`bin/local/validate-timeouts` holds the table's monopoly: it rejects a numeric
literal and a bare `None` on any `timeout=` argument under `ai/`, and rejects a
`proc.run` or `subprocess.run` call that writes no `timeout=` at all. Reading
only the bounds that were written down left the omission invisible, which is the
case this table exists to eliminate — a call with no bound is indistinguishable
from nobody having thought about one. Nothing under `ai/` is exempt.
`ai/claude/mcps/server.py` was, on the reading that running under
`uv run --no-project` put `ai/lib` out of its reach — but it puts that directory
on `sys.path` itself, which is how it imports `tool_registry`, so `timeouts`
came along with it and the exemption only meant the file went unchecked.

Three numbers deliberately stay outside it. `ci-check --wait-timeout`,
`eval_task.EVAL_CASE_BUDGET` and the MCP server's `TOOL_CALL_BUDGET` are
deadlines for work that could reasonably keep going, not bounds on a subprocess
that should already have answered; they say how long something is *worth*, which
is a different question.

Stdlib-only and importing nothing, so that `proc`, `git_client`, and everything
built on them can depend on it without a cycle.
"""

# doc-group: platform

from __future__ import annotations

# A subprocess that should answer instantly: a `--value-flags` probe, a session
# hook reading one file. Exceeding this is a wedged process, never real work.
QUICK = 5.0

# Flat-cost local reads — git metadata (`rev-parse`, `merge-base`, `log`,
# `grep`, `diff`), a `yq` parse. These scale with neither history nor tree size
# in any way that approaches ten seconds; `git grep` over a tree measures in
# tens of milliseconds.
LOCAL = 10.0

# One round trip to a remote: a single `gh api` call, a tracker CLI, an HTTP
# request. Bounded by latency rather than by payload, so a breach means the far
# end stopped answering.
NETWORK = 30.0

# Data-proportional work over a network — `fetch`, `gh api --paginate`. The
# transfer is as large as the history or the result set, so the bound is
# generous. It is a bound at all, unlike the operations below, because a socket
# can stall in a way that no amount of waiting resolves.
TRANSFER = 600.0

# No bound, because a bound would be wrong rather than merely large.
#
# Two kinds of work land here. Work proportional to the working tree —
# `git worktree add` materializes every file, `checkout` and `stash` write or
# restore as much of it as they touch, and `git add -A` reads and hashes it, so
# the honest bound is a function of the repo, not a constant. And work that
# executes someone else's code —
# `git commit` and `git push` run hooks in whatever repository is being operated
# on, which can be a linter, a secret scan, or a full test suite. `git rebase`
# is both at once, replaying commits with their hooks and rewriting the tree
# between them. In every case a timeout cannot distinguish a hang from a large
# input or a slow hook.
#
# Spelled as a named constant so that running unbounded stays a decision on the
# record. An omitted `timeout=` is indistinguishable from nobody having thought
# about it; `timeout=timeouts.UNBOUNDED` is greppable and reviewable, and
# `bin/local/validate-timeouts` rejects the bare `None` that would hide it.
UNBOUNDED = None
