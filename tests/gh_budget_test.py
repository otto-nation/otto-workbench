"""Tests for the GitHub budget breaker.

Exercised against a real `gh` on PATH — a stub the test writes — for the reason
`gh_client_test` gives: what matters here is how many subprocesses actually ran,
and a patched `subprocess` would let a breaker that short-circuits nothing pass
by returning the string the test expected.

`calls.txt` is the evidence throughout. The bug being fixed is a sweep that made
twenty-one calls against a budget the first one proved was gone, so "how many
times was gh invoked" is the assertion that distinguishes the fix from its
absence.
"""

import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from gh import budget  # noqa: E402
from gh import client as gh_client  # noqa: E402

EXHAUSTED = "GraphQL: API rate limit already exceeded for user ID 7399350."


def _refusing_gh(stub_gh, reset: int | None = None) -> Path:
    """A gh that refuses everything for quota, answering the probe with headers."""
    header = ""
    if reset is not None:
        header = (
            f'  printf "X-Ratelimit-Resource: graphql\\n" >&2\n'
            f'  printf "X-Ratelimit-Reset: {reset}\\n" >&2\n'
        )
    return stub_gh(
        'case "$*" in\n'
        "  *-i*)\n"
        f"{header}"
        f'    printf "{EXHAUSTED}\\n" >&2; exit 1 ;;\n'
        "esac\n"
        f'printf "{EXHAUSTED}\\n" >&2\n'
        "exit 1\n",
    )


def _non_probe_calls(calls: Path) -> list[str]:
    """Every recorded invocation that is not the reset-header probe."""
    if not calls.exists():
        return []
    return [line for line in calls.read_text().splitlines() if "-i" not in line.split()]


def test_a_second_call_after_an_exhausted_budget_is_not_made(stub_gh):
    """The bug: each caller spent a subprocess rediscovering a dead budget.

    Two `pr view`s, one real refusal — the second must not reach gh at all.
    """
    calls = _refusing_gh(stub_gh)

    gh_client.run("pr", "view", "1", "--repo", "o/r")
    gh_client.run("pr", "view", "2", "--repo", "o/r")

    assert len(_non_probe_calls(calls)) == 1


def test_the_short_circuited_result_says_no_call_was_made(stub_gh):
    """A refusal we invented must not read like one GitHub sent.

    Catches a future refactor that returns a bare CmdResult(returncode=1):
    the whole defence of a synthetic result is that it says what it is.
    """
    _refusing_gh(stub_gh)
    gh_client.run("pr", "view", "1", "--repo", "o/r")

    r = gh_client.run("pr", "view", "2", "--repo", "o/r")
    assert r.returncode == budget.BUDGET_LATCHED_RETURNCODE
    assert r.detail.startswith("no call made")
    assert "7399350" in r.detail


def test_the_remedy_is_explained_once_not_per_call(stub_gh, capsys):
    """Ten refused calls, one explanation.

    The symptom was six identical warnings in three seconds. A design that
    appends the hint per failure passes every other test here and fails this.
    """
    _refusing_gh(stub_gh)
    for i in range(10):
        gh_client.run("pr", "view", str(i), "--repo", "o/r")

    said = capsys.readouterr().err
    assert said.count("hourly GitHub API quota") == 1


def test_the_hint_does_not_recommend_the_endpoint_that_lies():
    """`gh api rate_limit` is exempt from the limit it reports on.

    Measured: it answered 5000/5000 with a reset 50 minutes wrong while every
    real GraphQL call was refused. Sending a user there is sending them to be
    told the budget is fine.
    """
    assert "rate_limit" not in budget.BUDGET_EXHAUSTED_HINT
    assert "X-Ratelimit-Reset" in budget.BUDGET_EXHAUSTED_HINT
    assert "X-Ratelimit-Reset" in budget.Latch(
        budget.Resource.GRAPHQL, "1", None).remedy()


def test_the_reset_time_comes_from_the_response_header(stub_gh):
    """The probe reads X-Ratelimit-Reset, which is the only truthful source."""
    at = int(time.time()) + 600
    _refusing_gh(stub_gh, reset=at)

    gh_client.run("pr", "view", "1", "--repo", "o/r")

    latch = budget.latched(budget.Resource.GRAPHQL)
    assert latch is not None
    assert latch.reset == float(at)
    assert time.strftime("%H:%M:%S", time.localtime(at)) in latch.remedy()


def test_no_reset_header_means_no_reset_time_is_claimed(stub_gh):
    """A guessed reset printed beside a real one is indistinguishable from it.

    So a probe that reads no header latches without claiming a time.
    """
    _refusing_gh(stub_gh, reset=None)

    gh_client.run("pr", "view", "1", "--repo", "o/r")

    latch = budget.latched(budget.Resource.GRAPHQL)
    assert latch is not None
    assert latch.reset is None
    assert "refills at" not in latch.remedy()


def test_a_reset_beyond_one_window_is_clamped(stub_gh):
    """A reset a day out is a clock disagreeing with the server's.

    Honouring it would refuse a budget that had already refilled, and the only
    remedy for that is to kill the process. The clamp is on how long the latch
    is *honoured*; what the server said is kept as-is, since the reported reset
    should not become a second guess of our own.
    """
    said = int(time.time()) + 86400
    _refusing_gh(stub_gh, reset=said)

    gh_client.run("pr", "view", "1", "--repo", "o/r")

    latch = budget.latched(budget.Resource.GRAPHQL)
    assert latch is not None
    assert latch.expires <= time.time() + 3600 + 1
    assert latch.reset == float(said)


def test_an_unclassifiable_argv_is_called_rather_than_refused(stub_gh):
    """Fail open. A call wrongly made costs one already-spent point; a call
    wrongly refused is indistinguishable from GitHub having nothing to say."""
    calls = _refusing_gh(stub_gh)
    gh_client.run("pr", "view", "1", "--repo", "o/r")
    before = len(_non_probe_calls(calls))

    gh_client.run("run", "download", "12345")

    assert len(_non_probe_calls(calls)) == before + 1


def test_a_latched_graphql_budget_does_not_refuse_rest(stub_gh):
    """REST and GraphQL are separate 5000-point budgets that refill apart."""
    calls = _refusing_gh(stub_gh)
    gh_client.run("pr", "view", "1", "--repo", "o/r")
    before = len(_non_probe_calls(calls))

    gh_client.run("api", "repos/o/r")

    assert len(_non_probe_calls(calls)) == before + 1


def test_the_latch_clears_once_the_reset_passes(stub_gh, monkeypatch):
    """The budget refills on its own, so the latch has to let go on its own."""
    calls = _refusing_gh(stub_gh, reset=int(time.time()) + 600)
    gh_client.run("pr", "view", "1", "--repo", "o/r")
    before = len(_non_probe_calls(calls))

    # Bound outside the lambda: reading `time.time` through the patched module
    # would call the replacement from inside itself.
    real_time = time.time
    monkeypatch.setattr(budget.time, "time", lambda: real_time() + 601)
    gh_client.run("pr", "view", "2", "--repo", "o/r")

    assert len(_non_probe_calls(calls)) == before + 1


def test_a_child_process_inherits_the_latch(stub_gh):
    """`pr` spawns claude-review, which spawns review-orchestrate.

    Each is a fresh interpreter with an empty table, so without this the
    grandchild pays again for what its parent already learned.
    """
    _refusing_gh(stub_gh, reset=int(time.time()) + 600)
    gh_client.run("pr", "view", "1", "--repo", "o/r")

    import os
    assert budget.LATCH_ENV in os.environ
    assert "graphql" in os.environ[budget.LATCH_ENV]


def test_an_inherited_latch_is_adopted(stub_gh, monkeypatch):
    """The other half: a child reads what its parent exported and honours it."""
    calls = stub_gh('printf "{}\\n"; exit 0')
    at = int(time.time()) + 600
    monkeypatch.setenv(budget.LATCH_ENV, f"graphql:{at}:{at}:7399350")

    budget.adopt_inherited()
    r = gh_client.run("pr", "view", "1", "--repo", "o/r")

    assert r.returncode == budget.BUDGET_LATCHED_RETURNCODE
    assert _non_probe_calls(calls) == []


def test_an_expired_inherited_latch_is_ignored(stub_gh, monkeypatch):
    """A latch whose window has passed is history, not a refusal."""
    calls = stub_gh('printf "{}\\n"; exit 0')
    at = int(time.time()) - 10
    monkeypatch.setenv(budget.LATCH_ENV, f"graphql:{at}:{at}:7399350")

    budget.adopt_inherited()
    gh_client.run("pr", "view", "1", "--repo", "o/r")

    assert len(_non_probe_calls(calls)) == 1


@pytest.mark.parametrize(
    "raw", ["garbage", "graphql", "graphql:notanumber:0:1", "graphql:1:2", ""])
def test_a_malformed_inherited_latch_is_ignored(stub_gh, monkeypatch, raw):
    """The variable is ordinary process state anything could have set.

    Ignoring a malformed one costs a wasted call, which is the direction this
    whole module errs in; raising would take down every `pr` invocation in a
    shell that happened to have it set.
    """
    calls = stub_gh('printf "{}\\n"; exit 0')
    monkeypatch.setenv(budget.LATCH_ENV, raw)

    budget.adopt_inherited()
    gh_client.run("pr", "view", "1", "--repo", "o/r")

    assert len(_non_probe_calls(calls)) == 1


def test_an_ordinary_failure_does_not_arm_the_latch(stub_gh):
    """A 404 is an answer. Only a quota refusal closes the gate."""
    calls = stub_gh('printf "gh: Not Found\\n" >&2\nexit 1\n')

    gh_client.run("pr", "view", "1", "--repo", "o/r")
    gh_client.run("pr", "view", "2", "--repo", "o/r")

    assert budget.latched(budget.Resource.GRAPHQL) is None
    assert len(_non_probe_calls(calls)) == 2


def test_resource_for_maps_the_calls_that_actually_bleed():
    """`gh pr view` spends the GraphQL budget while reading like neither
    `api` nor `graphql` — it is the call the GC sweep made twenty-one times."""
    assert budget.resource_for(("pr", "view", "1")) is budget.Resource.GRAPHQL
    assert budget.resource_for(("api", "graphql")) is budget.Resource.GRAPHQL
    assert budget.resource_for(("api", "repos/o/r")) is budget.Resource.CORE
    assert budget.resource_for(("run", "download", "1")) is None
    assert budget.resource_for(()) is None


def test_a_budget_that_dies_mid_ladder_stops_the_remaining_attempts(
        tmp_path, monkeypatch, stub_gh):
    """A secondary limit earns five attempts; an exhausted budget earns none.

    When the first turns into the second partway up the ladder, the remaining
    attempts are spent against a quota that is gone — nine minutes of waiting
    to report the failure already in hand. The breaker ends the ladder because
    the short-circuited attempt is not retryable.
    """
    calls = stub_gh(
        f'n=$(wc -l < {tmp_path / "calls.txt"})\n'
        'if [ "$n" -le 1 ]; then\n'
        '  printf "You have exceeded a secondary rate limit\\n" >&2; exit 1\n'
        "fi\n"
        f'printf "{EXHAUSTED}\\n" >&2\n'
        "exit 1\n",
    )
    monkeypatch.setattr(gh_client, "sleep", lambda _n: None)

    gh_client.graphql("query {}")

    # The secondary-limit attempt and the one that found the budget gone. The
    # three the ladder still had are not spent.
    assert len(_non_probe_calls(calls)) == 2


def test_threads_meeting_the_same_refusal_probe_once(stub_gh, capsys):
    """`fetch_pr_context` and the review pipeline both fan out over a pool.

    Without the placeholder entry the arming path writes before it probes,
    every thread in flight would start its own probe and print its own
    warning — the warning storm again, arriving by a different route.
    """
    import threading

    calls = _refusing_gh(stub_gh)
    workers = [
        threading.Thread(target=gh_client.run, args=("pr", "view", str(i)))
        for i in range(8)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    recorded = calls.read_text().splitlines()
    assert sum(1 for line in recorded if "-i" in line.split()) == 1
    assert capsys.readouterr().err.count("budget exhausted") == 1


@pytest.mark.parametrize("argv", [
    ("search", "prs", "--repo", "o/r"),
    ("api", "search/issues?q=repo:o/r+sym+is:merged"),
])
def test_a_search_call_is_not_attributed_to_a_budget_it_does_not_spend(argv):
    """Search is a third budget: 30/min, and it clears on its own.

    Its exhaustion message is worded identically to the primary quota's, so
    matching it and guessing a resource would latch the whole GraphQL or core
    budget for an hour on the strength of a throttle that lifts in seconds.
    Measured: one `gh search prs` fires a GraphQL request and a REST call to
    /search/issues, returning `X-Ratelimit-Resource: graphql` and `search`.
    `pr.supersession` reaches the same budget through `gh api search/issues`.
    """
    assert budget.resource_for(argv) is None


def test_a_search_refusal_does_not_latch_the_core_budget(stub_gh):
    """The live path: a throttled `gh api search/issues` must not stop the
    ordinary REST calls behind it.

    Two searches rather than a search and a REST read: a real core refusal
    *should* latch, so mixing the two would pass on the strength of the wrong
    call. Both searches must go out.
    """
    calls = _refusing_gh(stub_gh)

    gh_client.api("search/issues?q=repo:o/r+one+is:merged")
    gh_client.api("search/issues?q=repo:o/r+two+is:merged")

    assert budget.latched(budget.Resource.CORE) is None
    assert len(_non_probe_calls(calls)) == 2
