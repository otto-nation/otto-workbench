"""How long a subprocess may run, decided once instead of at every call site.

`ai/` decided this in 22 places and four different ways: constants scoped to one
module, bare literals at the call site, a caller-supplied argument, and — for
most of the git client — nothing at all. The same operation got a different
bound depending on which file it was called from; a single `gh api` round trip
was 30s in `gh.pr_reads`, 10s in `pr-rebase`, and 10s in `retro-scan`. No
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
| `QUICK` | A session hook reading one file | Should answer instantly; a breach is a wedged process, never real work. |
| `LOCAL` | Flat-cost local reads — `rev-parse`, `merge-base`, `log`, `grep`, `diff`, a `yq` parse | Scales with neither history nor tree size in any way that approaches the bound. |
| `NETWORK` | One round trip — a single `gh api` call, a tracker CLI, an HTTP request | Bounded by latency, not payload, so a breach means the far end stopped answering. |
| `LAUNCH` | Starting a heavyweight local process — a Node program that loads its extensions before it answers (`pi --list-models`, `pi --version`), or a short script that starts several interpreters (`mise hook-env` restore) | Flat cost, but the cost is process start-up, which a loaded machine stretches several-fold; a breach still means a hang. |
| `TRANSFER` | Data-proportional over a socket — `fetch`, `gh api --paginate` | As large as the history or the result set, but a socket can stall in a way waiting will not fix. |
| `REFRESH` | A package or self-update that downloads and installs (`pi update`) | Bounded to catch a stall, not to size the work — git fetch and npm install can hang on a dead socket. |
| `UNBOUNDED` | `worktree add`, `commit`, `push`, `rebase`, `checkout`, `stash`, `add`, `status` | A bound would be wrong, not merely large. |

For QUICK, LOCAL, NETWORK and LAUNCH the cost is the same whatever the
repository holds, so a breach means something is genuinely wrong — a hang, a
dead socket, a deadlock — and a timeout is a hang detector. TRANSFER and
REFRESH cost whatever the payload costs, and the bound is generous so healthy
work finishes; a breach is still a stall, not a large input. UNBOUNDED is the
remaining case, where a fixed timeout silently converts a large repo into a
broken tool, which is why it exists and is spelled out rather than omitted.

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
`eval.task.EVAL_CASE_BUDGET` and the MCP server's `TOOL_CALL_BUDGET` are
deadlines for work that could reasonably keep going, not bounds on a subprocess
that should already have answered; they say how long something is *worth*, which
is a different question.

Stdlib-only and importing nothing, so that `proc`, `git.client`, and everything
built on them can depend on it without a cycle.
"""

# doc-group: platform

from __future__ import annotations

# A subprocess that should answer instantly: a session hook reading one file.
# Exceeding this is a wedged process, never real work.
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

# Starting a local CLI whose own start-up dominates: pi is a Node program
# that loads its extensions before it prints anything. `pi --list-models`
# measured 6.4s on an idle machine and 12-17s at a load average of 14 on
# four cores, so LOCAL's ten seconds killed listings that were healthy, only
# slow. The same bound covers a short restore script that starts bash, two
# Python interpreters, and `mise hook-env` before it answers. The cost is
# still flat — it does not grow with any input — so a breach of this bound
# is a hang, not a large answer.
LAUNCH = 60.0

# Data-proportional work over a network — `fetch`, `gh api --paginate`. The
# transfer is as large as the history or the result set, so the bound is
# generous. It is a bound at all, unlike the operations below, because a socket
# can stall in a way that no amount of waiting resolves.
TRANSFER = 600.0

# A package or self-update that downloads and installs. The work is as large
# as the payload, so the bound is generous: it exists to catch a stall, not
# to size the work. `pi update` fetches git clones and runs npm install with
# no timeout of its own; an unattended sync that waits forever for a hung
# socket blocks every later maintenance run.
REFRESH = 900.0

# No bound, because a bound would be wrong rather than merely large.
#
# Two kinds of work land here. Work proportional to the working tree —
# `git worktree add` materializes every file, `checkout` and `stash` write or
# restore as much of it as they touch, `git add -A` reads and hashes it, and
# `git status` stats every file in it, so the honest bound is a function of the
# repo, not a constant. And work that executes someone else's code — `git commit`
# and `git push` run hooks in whatever repository is being operated on, which
# can be a linter, a secret scan, or a full test suite. `git rebase` is both at
# once, replaying commits with their hooks and rewriting the tree
# between them. In every case a timeout cannot distinguish a hang from a large
# input or a slow hook.
#
# Spelled as a named constant so that running unbounded stays a decision on the
# record. An omitted `timeout=` is indistinguishable from nobody having thought
# about it; `timeout=timeouts.UNBOUNDED` is greppable and reviewable, and
# `bin/local/validate-timeouts` rejects the bare `None` that would hide it.
UNBOUNDED = None
