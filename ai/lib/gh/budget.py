"""What we know about this account's GitHub API quota, and what follows from it.

A scheduled `pr gc` produced this, six times in three seconds, and then
reported success:

    ⚠ GC: gh could not report usemaximum/maximum#3659 (GraphQL: API rate limit
      already exceeded for user ID 7399350.) — leaving it in place
    ▸ GC: nothing to clean

Each line is a round trip that could not have succeeded. The first one proved
the budget was gone; the rest spent a subprocess each to be told so again. Two
prune loops at ten PRs apiece, plus the context resolution in front of them,
put the ceiling at about twenty-one.

So this module owns one fact — *this resource is refused until it refills* —
and the two things that follow from it: a call we have already proven will be
refused is not made, and the remedy is explained once rather than once per
call. That second half is why the hint lives here and not in
`client._error_message`, where it used to. The hint is a fact about the budget,
not about the call; appended per failure it reproduces the warning storm above.
It was also unreachable there, since `_error_message`'s only production caller
is the retry-waiting log and an exhausted budget is deliberately never retried.

Three things about this failure are worth writing down, because each one is
expensive to rediscover:

`gh api rate_limit` lies. It is exempt from the limit it reports on, so it
answers cheerfully while every real call is refused. Measured at one instant,
with 5000/5000 GraphQL points spent:

    gh api graphql -i    X-Ratelimit-Remaining: 0      reset 15:30:15
    gh api rate_limit    remaining: 5000               reset 16:21:12

Both numbers wrong, including the reset. The only true reading is the
`X-Ratelimit-*` headers on a call that actually went out, which is why the
reset time here comes from a probe rather than from that endpoint, and why
nothing in this module or its hint recommends it.

A refused request is not charged. That is what makes the probe free: the call
that arms this latch has already failed, and asking once more with `-i` to read
the reset costs a subprocess and no quota.

REST and GraphQL are separate budgets, 5000 each, and `gh pr view` spends the
GraphQL one despite looking like neither. A latch keyed on the wrong resource
would block the wrong half of the API, so `resource_for` maps argv to a budget
and anything it does not recognise is called rather than refused. Failing open
costs one wasted call; failing closed invents an outage.
"""

# doc-group: platform

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from enum import StrEnum

from core import log
from core.proc import CmdResult

# What `run` reports for a call it declined to make. Distinct from a real
# failure's exit code so a reader can tell "GitHub said no" from "we did not
# ask", and in the same family as `GH_MISSING_RETURNCODE`: both are answers
# produced locally for a subprocess that never ran.
#
# 69 rather than a number in the 124-127 band: those are shell conventions for
# things a process did (timed out, was signalled, could not be executed), and
# borrowing one would have this read as a failed exec of gh. This never becomes
# a process's own exit status — it is carried inside a CmdResult and compared
# against by name — so it only has to be a value gh itself will not return.
BUDGET_LATCHED_RETURNCODE = 69

LATCH_ENV = "WORKBENCH_GH_BUDGET_LATCH"

# How GitHub words an exhausted *primary* budget — the hourly quota, which is
# per user and counted separately for REST and GraphQL. Moved here from
# `client`, with the classifier, because arming the latch is now the only thing
# that reads them.
_BUDGET_EXHAUSTED_MARKERS = (
    "api rate limit exceeded",
    "api rate limit already exceeded",
)

# The account the refusal names. Parsed rather than asked for: `gh api user`
# would cost a REST call to learn something the error already says, and on a
# machine whose REST budget is also thin that is a call spent on bookkeeping.
_USER_ID_RE = re.compile(r"user id (\d+)", re.IGNORECASE)

_RESET_HEADER_RE = re.compile(r"^x-ratelimit-reset:\s*(\d+)\s*$", re.IGNORECASE | re.MULTILINE)
_RESOURCE_HEADER_RE = re.compile(r"^x-ratelimit-resource:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)

# A GitHub reset window is an hour, so a reset further out than that is a clock
# disagreeing with the server rather than a real wait. Honouring it would latch
# against a budget that has already refilled, and the only remedy a user has
# for that is to find and kill the process. Clamped instead.
_MAX_LATCH_SECONDS = 3600.0


class Resource(StrEnum):
    """The two primary budgets, which refill independently of each other."""

    GRAPHQL = "graphql"
    CORE = "core"


@dataclass(frozen=True)
class Latch:
    """One resource known to be refused, and when it is expected back.

    ``reset`` is None when the probe could not read a reset header. The latch
    still holds — the refusal is the evidence, not the header — but nothing
    claims a time it does not have, since a guessed reset printed next to a
    real one is indistinguishable from it.
    """

    resource: Resource
    user_id: str
    reset: float | None
    # Defaults to the past, so a Latch built without one is expired rather than
    # eternal. A latch is a refusal to make calls: the harmless direction for a
    # missing field is the one that lets them through.
    expires: float = 0.0

    @property
    def expired(self) -> bool:
        """Whether the window this latch describes has passed.

        `expires` rather than `reset` because the two answer different
        questions: `reset` is what the server said and is None when the probe
        read no header, while `expires` is when we stop honouring the latch and
        always has a value. Deriving expiry from `reset` alone made a latch
        with no header expire instantly — `None or 0.0` is in the past — so the
        breaker silently did nothing in exactly the case where the probe had
        failed.
        """
        return time.time() >= self.expires

    def remedy(self) -> str:
        """The hint, with the reset time when the probe read one."""
        if self.reset is None:
            return BUDGET_EXHAUSTED_HINT
        at = time.strftime("%H:%M:%S", time.localtime(self.reset))
        return f"{BUDGET_EXHAUSTED_HINT}; this one refills at {at}"


# Said once, when the latch arms. Deliberately does not name the exempt
# endpoint that reports on this: see the module docstring for the measurement
# showing it answers with a full budget, and the wrong reset, while every real
# call is refused. Naming it even to warn against it puts the string in front
# of a reader who will then run it, so the hint points only at the header.
BUDGET_EXHAUSTED_HINT = (
    "the hourly GitHub API quota for this account is spent — it refills on its "
    "own, and another token for the same user shares it. The X-Ratelimit-Reset "
    "header on a real call is the only truthful reading of when it returns"
)

# Guards the table below. `fetch_pr_context` and the review pipeline both fan
# out over a ThreadPoolExecutor, so several threads can meet the same refusal
# at once; first arm wins and the rest are no-ops, as in `publishing.hold`, so
# the reported reset does not jitter between two readings of the same outage.
_lock = threading.Lock()
_latched: dict[Resource, Latch] = {}

# gh subcommands that resolve through GraphQL whatever their argv looks like.
# `gh pr view` is the one that surprises people — it spends the GraphQL budget
# while reading like neither `api` nor `graphql`, and it is the call the GC
# sweep was making twenty-one times.
_GRAPHQL_COMMANDS = frozenset({"pr", "repo", "issue", "label", "search"})


def is_budget_exhausted(said: str) -> bool:
    """Whether the primary hourly quota is gone, for REST or for GraphQL."""
    lower = said.lower()
    return any(marker in lower for marker in _BUDGET_EXHAUSTED_MARKERS)


def resource_for(args: tuple[str, ...]) -> Resource | None:
    """Which budget this argv spends, or None when we cannot tell.

    None means *make the call*. The map is a short list and it will not cover
    every subcommand gh grows, so the default has to be the safe direction: a
    call wrongly made costs one already-spent point and a 403 the caller
    already handles, while a call wrongly refused is indistinguishable from
    GitHub having nothing to say.
    """
    if not args:
        return None
    if args[0] == "api":
        rest = args[1:]
        if not rest:
            return None
        return Resource.GRAPHQL if rest[0] == "graphql" else Resource.CORE
    if args[0] in _GRAPHQL_COMMANDS:
        return Resource.GRAPHQL
    return None


def latched(resource: Resource | None) -> Latch | None:
    """The live latch for *resource*, or None when it may be called.

    Clears the entry once its window passes, so the caller that notices the
    expiry is the one that pays for the next real attempt.
    """
    if resource is None:
        return None
    with _lock:
        latch = _latched.get(resource)
        if latch is None:
            return None
        if latch.expired:
            del _latched[resource]
            _export()
            return None
        return latch


def latched_result(latch: Latch) -> CmdResult:
    """The answer for a call we declined to make.

    Shaped like a real failure so every existing caller reads it the way it
    already reads a refusal — the same trade `client.run` makes for a missing
    gh. What keeps that from being a lie is the wording: "no call made" is the
    first thing in the detail, so a reader debugging an empty result finds the
    reason in the message rather than inferring a round trip that never
    happened.
    """
    return CmdResult(
        returncode=BUDGET_LATCHED_RETURNCODE,
        stderr=(
            f"no call made — the {latch.resource.value} budget for user "
            f"{latch.user_id} is spent ({latch.remedy()})"
        ),
    )


def _probe_reset(resource: Resource) -> tuple[float | None, Resource]:
    """Read X-Ratelimit-Reset from one more refused call. Free: 403s are not charged.

    Returns the authoritative resource too, since `X-Ratelimit-Resource` names
    the budget that was actually charged and is better evidence than our own
    reading of the argv.

    Imported here rather than at module scope: `client` imports this module to
    consult the latch, so the dependency has to run one way at import time and
    the other way only when a probe fires.
    """
    from gh import client as gh_client

    argv = (
        ("api", "graphql", "-i", "-f", "query=query { viewer { login } }")
        if resource is Resource.GRAPHQL
        else ("api", "-i", "user")
    )
    r = gh_client.run(*argv, _skip_breaker=True)
    said = r.combined_output
    found = _RESOURCE_HEADER_RE.search(said)
    charged = resource
    if found:
        try:
            charged = Resource(found.group(1).lower())
        except ValueError:
            # A resource we do not model (`search`, `integration_manifest`).
            # Keep our own reading rather than inventing an enum member.
            charged = resource
    reset = _RESET_HEADER_RE.search(said)
    return (float(reset.group(1)) if reset else None), charged


def arm(said: str, resource: Resource | None) -> None:
    """Record that *resource* is refused, and say so once.

    *said* is the failing call's combined output, which carries the account the
    refusal names. A refusal we cannot attribute to a user is still a refusal,
    so an unparseable ID latches under "unknown" rather than being dropped —
    the alternative is to keep making calls because the message was worded
    unexpectedly.
    """
    if resource is None:
        return
    with _lock:
        if resource in _latched and not _latched[resource].expired:
            return
        # Placeholder first: the probe below re-enters `client.run`, and
        # without an entry here a second thread meeting the same refusal would
        # start its own probe.
        found = _USER_ID_RE.search(said)
        user_id = found.group(1) if found else "unknown"
        ceiling = time.time() + _MAX_LATCH_SECONDS
        _latched[resource] = Latch(resource, user_id, None, ceiling)

    reset, charged = _probe_reset(resource)
    # Clamp: a reset beyond one window is a local clock disagreeing with the
    # server's, and honouring it would refuse a budget that has refilled. A
    # probe that read no header falls back to the same ceiling, so the latch
    # always has an expiry even when it cannot report a reset.
    ceiling = time.time() + _MAX_LATCH_SECONDS
    expires = min(reset, ceiling) if reset is not None else ceiling
    latch = Latch(charged, user_id, reset, expires)

    with _lock:
        _latched[charged] = latch
        if charged is not resource:
            _latched.pop(resource, None)
        _export()

    log.warn(
        f"GitHub {charged.value} budget exhausted — no further {charged.value} "
        f"calls will be made this run ({latch.remedy()})"
    )


def _export() -> None:
    """Publish the table to the environment children inherit. Caller holds the lock.

    `pr` delegates to `claude-review`, which spawns `review-orchestrate`, and
    each is a fresh interpreter with its own empty table. Passing the latch
    down the tree is what stops the grandchild re-learning a refusal its parent
    already met.

    Format is `resource:expires:reset:user_id`, space separated. Expiry and
    reset are both carried because they are different facts: expiry always has
    a value and is what decides whether the latch still holds, while a reset of
    0 means the probe read no header and nothing should claim a time.
    """
    # ceiling: the latch spans this process and the children it spawns, so a
    # second `pr` invocation in the same reset window rediscovers the spent
    # budget once more. Upgrade trigger: persist it under
    # cache_dir("gh-budget") keyed on (user id, resource), once no caller reads
    # a refused call as an authoritative empty answer — today
    # `_check_existing_pending`, `dedup.fetch_bot_reviews`, `landed.merged_pr`
    # and `_drain_thread_pages` past page one all do, and a false refusal there
    # duplicates a review, force-pushes over merged work, or drops triage state.
    if not _latched:
        os.environ.pop(LATCH_ENV, None)
        return
    os.environ[LATCH_ENV] = " ".join(
        f"{latch.resource.value}:{int(latch.expires)}:{int(latch.reset or 0)}:{latch.user_id}"
        for latch in _latched.values()
    )


def _parse_entry(entry: str) -> Latch | None:
    """One `resource:expires:reset:user_id` record, or None if it is malformed.

    Returns rather than raises: the variable is ordinary process state that
    anything could have set, and the cost of ignoring a bad record is one
    wasted call — the direction this whole module errs in. Raising would take
    down every `pr` invocation in a shell that happened to have it set.
    """
    parts = entry.split(":")
    if len(parts) != 4:
        return None
    name, expires, reset, user_id = parts
    try:
        resource = Resource(name)
        # Clamped again on the way in: the exporting process is not necessarily
        # this one, and an inherited expiry is as much a claim about some
        # machine's clock as a header was.
        until = min(float(expires), time.time() + _MAX_LATCH_SECONDS)
        at = float(reset) or None
    except ValueError:
        return None
    return Latch(resource, user_id, at, until)


def adopt_inherited() -> None:
    """Take up the latches our parent process already paid to discover."""
    raw = os.environ.get(LATCH_ENV, "")
    if not raw:
        return
    parsed = (_parse_entry(entry) for entry in raw.split())
    live = [latch for latch in parsed if latch is not None and not latch.expired]
    with _lock:
        _latched.update({latch.resource: latch for latch in live})


def reset_for_tests() -> None:
    """Drop every latch and the variable carrying it.

    A latch that outlives its test short-circuits every stubbed gh call in the
    tests that follow, which is a failure that depends on collection order.
    `tests/conftest.py` calls this from an autouse fixture for that reason.
    """
    with _lock:
        _latched.clear()
        os.environ.pop(LATCH_ENV, None)
