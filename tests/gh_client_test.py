"""Tests for the gh client.

The runner is exercised against a real `gh` on PATH — a stub script the test
writes — rather than a patched `subprocess`. A mock returning the string the
test author expected passes whether or not the flag combination is right, and
the flags are most of what this module decides. The argv builder, the timeout
tiers and the retry classifier are pure, so they are asserted directly.

`time.sleep` is patched wherever a ladder runs. The waits are minutes by
design, and asserting the sleep durations is a stronger check than waiting
them out anyway.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import gh.budget  # noqa: E402
import gh.client  # noqa: E402
import gh.pr_reads  # noqa: E402
import core.proc  # noqa: E402
import core.timeouts  # noqa: E402
from core.proc import CmdResult  # noqa: E402


@pytest.fixture
def no_sleep(monkeypatch) -> list[float]:
    """Collect the ladder's waits instead of serving them.

    The client holds its own `sleep` name so this replaces only the waits it
    asks for. Patching `time.sleep` itself would also catch the millisecond
    polling `subprocess` does while waiting on a bounded child, which lands a
    stray 0.001 in the middle of the ladder.
    """
    slept: list[float] = []
    monkeypatch.setattr(gh.client, "sleep", slept.append)
    return slept


# ── Timeout policy ──────────────────────────────────────────────────────────


def test_a_single_round_trip_takes_the_network_tier():
    assert gh.client._timeout_for(("api", "user")) == core.timeouts.NETWORK


def test_pagination_takes_the_transfer_tier():
    """--paginate walks as many requests as the result set needs."""
    assert gh.client._timeout_for(("api", "--paginate", "repos/o/r/pulls")) == core.timeouts.TRANSFER


def test_an_artifact_download_takes_the_transfer_tier():
    assert gh.client._timeout_for(("run", "download", "42")) == core.timeouts.TRANSFER


def test_a_failed_log_bundle_takes_the_transfer_tier():
    assert gh.client._timeout_for(("run", "view", "42", "--log-failed")) == core.timeouts.TRANSFER


def test_a_job_log_endpoint_takes_the_transfer_tier():
    """Short endpoint, whole log in the body."""
    assert gh.client._timeout_for(
        ("api", "repos/o/r/actions/jobs/7/logs"),
    ) == core.timeouts.TRANSFER


def test_a_run_view_without_logs_stays_on_the_network_tier():
    assert gh.client._timeout_for(("run", "view", "42", "--json", "jobs")) == core.timeouts.NETWORK


def test_an_empty_argv_still_resolves_a_tier():
    assert gh.client._timeout_for(()) == core.timeouts.NETWORK


def test_run_takes_no_timeout_from_its_caller():
    """The bound is the client's to decide, so there is nothing to override."""
    with pytest.raises(TypeError):
        gh.client.run("api", "user", timeout=1)


# ── Retry classification ────────────────────────────────────────────────────


def test_a_success_earns_no_ladder():
    assert gh.client._ladder_for(CmdResult()) is None


@pytest.mark.parametrize("said", [
    "You have exceeded a secondary rate limit",
    "triggered an abuse detection mechanism",
    "Please retry later",
])
def test_a_throttle_earns_the_rate_limit_ladder(said):
    r = CmdResult(returncode=1, stdout=said)
    assert gh.client._ladder_for(r) is gh.client.RATE_LIMIT_LADDER


@pytest.mark.parametrize("said", [
    '{"message": "Forbidden"}',
    '{"message": "Forbidden", "documentation_url": "https://docs.github.com/rest"}',
    '{"message": "Resource protected by organization SAML enforcement"}',
])
def test_a_permission_denial_is_an_answer_not_a_throttle(said):
    """A 403 that is not a rate limit will say the same thing in an hour.

    A bare "forbidden" marker used to put these on the rate-limit ladder, so a
    missing OAuth scope or an invisible repo cost four sleeps totalling eight
    minutes before reporting the denial — a hard blocker the caller needs at
    once.
    """
    r = CmdResult(returncode=1, stdout=said)
    assert gh.client._ladder_for(r) is None


@pytest.mark.parametrize("said", [
    "GraphQL: API rate limit already exceeded for user ID 7399350.",
    '{"message": "API rate limit exceeded for user ID 7399350."}',
])
def test_an_exhausted_budget_is_not_retried(said):
    """The hourly quota resets up to an hour out; every ladder here gives up in
    under nine minutes, so retrying only spends attempts on a budget that is
    already gone."""
    r = CmdResult(returncode=1, stderr=said)
    assert gh.client._ladder_for(r) is None
    assert gh.budget.is_budget_exhausted(said)


# The remedy used to be asserted here, against `_error_message`. That test
# could not fail when its subject broke: `_error_message` is only reached from
# the retry-waiting log, and an exhausted budget is never retried, so the
# branch it covered was unreachable in production. Saying the remedy is now
# `gh.budget`'s job, and `tests/gh_budget_test.py` asserts it against the path
# that actually runs.


def test_an_ordinary_failure_gets_no_hint():
    r = CmdResult(returncode=1, stdout='{"message": "Not Found"}')
    assert gh.client._error_message(r) == "Not Found"


def test_a_server_error_earns_the_transient_ladder():
    r = CmdResult(returncode=1, stderr="HTTP 503: Service Unavailable")
    assert gh.client._ladder_for(r) is gh.client.TRANSIENT_LADDER


def test_a_timeout_earns_the_transient_ladder():
    r = CmdResult(returncode=core.proc.TIMEOUT_RETURNCODE, stderr="timed out after 30s")
    assert gh.client._ladder_for(r) is gh.client.TRANSIENT_LADDER


def test_a_not_found_earns_no_ladder():
    """A 4xx is an answer, not a failure.

    The ladder this replaces retried any non-zero exit five times with a flat
    five-second delay, so a branch with no PR yet cost twenty seconds to learn
    it had no PR.
    """
    r = CmdResult(returncode=1, stdout='{"message": "Not Found"}')
    assert gh.client._ladder_for(r) is None


def test_the_rate_limit_ladder_backs_off_and_caps():
    ladder = gh.client.RATE_LIMIT_LADDER
    waits = [ladder.wait(n) for n in range(ladder.attempts)]
    assert waits == sorted(waits)
    assert waits[0] == 60.0
    assert max(waits) <= ladder.max_wait


def test_the_transient_ladder_is_short_enough_not_to_look_wedged():
    ladder = gh.client.TRANSIENT_LADDER
    assert sum(ladder.wait(n) for n in range(ladder.attempts - 1)) <= 10.0


# ── Retry loop ──────────────────────────────────────────────────────────────


def test_a_throttle_is_retried_until_it_clears(tmp_path, stub_gh, no_sleep):
    """The stub reports a secondary rate limit twice, then answers."""
    counter = tmp_path / "n"
    stub_gh(f"""
n=$(cat {counter} 2>/dev/null || echo 0)
echo $((n + 1)) > {counter}
if [ "$n" -lt 2 ]; then
  echo 'You have exceeded a secondary rate limit. Please retry later.'
  exit 1
fi
echo '{{"login": "octocat"}}'
""")
    r = gh.client.api("user")
    assert r.ok
    assert json.loads(r.stdout)["login"] == "octocat"
    assert len(no_sleep) == 2


def test_a_not_found_is_returned_on_the_first_attempt(stub_gh, no_sleep):
    calls = stub_gh("""
echo '{"message": "Not Found"}'
exit 1
""")
    r = gh.client.api("repos/o/r/pulls/9999")
    assert not r.ok
    assert calls.read_text().count("\n") == 1
    assert no_sleep == []


def test_a_throttle_that_never_clears_gives_up_and_returns_it(stub_gh, no_sleep):
    stub_gh("""
echo 'You have exceeded a secondary rate limit'
exit 1
""")
    r = gh.client.api("user")
    assert not r.ok
    assert len(no_sleep) == gh.client.RATE_LIMIT_LADDER.attempts - 1


def test_retry_off_makes_exactly_one_attempt(stub_gh, no_sleep):
    calls = stub_gh("""
echo 'You have exceeded a secondary rate limit'
exit 1
""")
    assert not gh.client.api("user", retry=False).ok
    assert calls.read_text().count("\n") == 1
    assert no_sleep == []


def test_an_unresolvable_line_raises_rather_than_retrying(stub_gh, no_sleep):
    """Not a transport failure: the diff moved, so the caller must re-anchor."""
    stub_gh("""
echo '{"message": "line could not be resolved to a diff position"}'
exit 1
""")
    with pytest.raises(gh.client.LineResolutionError):
        gh.client.api("repos/o/r/pulls/1/comments", method="POST")
    assert no_sleep == []


# ── Runner ──────────────────────────────────────────────────────────────────


def test_a_missing_gh_is_a_result_rather_than_an_exception(tmp_path, monkeypatch):
    """Three call sites caught FileNotFoundError and forty-two did not."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    r = gh.client.run("api", "user")
    assert r.returncode == gh.client.GH_MISSING_RETURNCODE
    assert "not installed" in r.stderr
    assert gh.client.out("api", "user", default="unknown") == "unknown"


def test_run_carries_stderr_so_a_caller_can_name_the_cause(stub_gh):
    stub_gh("echo 'HTTP 503: upstream is down' >&2; exit 1")
    r = gh.client.run("api", "user")
    assert not r.ok
    assert "503" in r.detail
    assert r.server_error


def test_out_strips_and_returns_stdout(stub_gh):
    stub_gh("echo '  octocat  '")
    assert gh.client.out("api", "user") == "octocat"


def test_ok_reads_the_exit_code(stub_gh):
    stub_gh("exit 0")
    assert gh.client.ok("auth", "status")


def test_lines_drops_blanks(stub_gh):
    stub_gh("printf 'a\\n\\nb\\n'")
    assert gh.client.lines("api", "user") == ["a", "b"]


def test_json_out_falls_back_when_the_output_is_not_json(stub_gh):
    stub_gh("echo '<html>gateway timeout</html>'")
    assert gh.client.json_out("api", "user", default={"x": 1}) == {"x": 1}


def test_json_out_falls_back_on_a_failed_call(stub_gh, no_sleep):
    stub_gh("exit 1")
    assert gh.client.json_out("api", "user", default=[]) == []


def test_stdin_reaches_gh(stub_gh):
    stub_gh("cat")
    r = gh.client.run("api", "graphql", input_text='{"query": "x"}')
    assert r.stdout == '{"query": "x"}'


# ── API argv ────────────────────────────────────────────────────────────────


def test_a_get_is_just_the_endpoint():
    assert gh.client._api_argv(
        "user", "GET", "", False, False, None, None, None,
    ) == ("api", "user")


def test_a_write_names_its_method():
    assert gh.client._api_argv(
        "repos/o/r/issues", "POST", "", False, False, None, None, None,
    ) == ("api", "repos/o/r/issues", "--method", "POST")


def test_typed_and_raw_fields_use_different_flags():
    """-F lets gh detect an integer; -f keeps it a string."""
    argv = gh.client._api_argv(
        "graphql", "GET", "", False, False, None, {"number": "7"}, {"query": "q"},
    )
    assert "-f" in argv and "query=q" in argv
    assert "-F" in argv and "number=7" in argv


def test_headers_are_passed_one_per_flag():
    argv = gh.client._api_argv(
        "repos/o/r/pulls/1", "GET", "", False, False,
        {"Accept": "application/vnd.github.v3.diff"}, None, None,
    )
    assert "--header" in argv
    assert "Accept: application/vnd.github.v3.diff" in argv


def test_paginate_and_slurp_both_reach_the_argv():
    argv = gh.client._api_argv(
        "repos/o/r/pulls/1/comments", "GET", "", True, True, None, None, None,
    )
    assert "--paginate" in argv and "--slurp" in argv


def test_a_jq_expression_reaches_the_argv():
    argv = gh.client._api_argv("user", "GET", ".login", False, False, None, None, None)
    assert argv[-2:] == ("--jq", ".login")


def test_a_body_on_stdin_names_itself_in_the_argv():
    """gh ignores stdin without `--input -`, and sends an empty body instead."""
    argv = gh.client._api_argv(
        "repos/o/r/pulls/1/reviews", "POST", "", False, False, None, None, None,
        body_on_stdin=True,
    )
    assert argv == ("api", "repos/o/r/pulls/1/reviews", "--method", "POST", "--input", "-")


def test_escape_sequences_are_refused_unless_asked_for():
    """gh exits 1 on a response carrying terminal escapes rather than printing it."""
    plain = gh.client._api_argv(
        "repos/o/r/actions/jobs/1/logs", "GET", "", False, False, None, None, None,
    )
    allowed = gh.client._api_argv(
        "repos/o/r/actions/jobs/1/logs", "GET", "", False, False, None, None, None,
        allow_escape_sequences=True,
    )
    assert "--allow-escape-sequences" not in plain
    assert "--allow-escape-sequences" in allowed


# ── Request bodies ──────────────────────────────────────────────────────────


def test_api_sends_its_body_on_stdin(stub_gh):
    calls = stub_gh("cat")
    r = gh.client.api(
        "repos/o/r/pulls/1/reviews", method="POST", input_text='{"event": "COMMENT"}',
    )
    assert r.stdout == '{"event": "COMMENT"}'
    assert "--input -" in calls.read_text()


def test_api_without_a_body_asks_gh_to_read_nothing(stub_gh):
    calls = stub_gh("echo '{}'")
    gh.client.api("user")
    assert "--input" not in calls.read_text()


def test_graphql_sends_a_whole_document_on_stdin(stub_gh):
    """A mutation with a nested variable does not fit gh's -f/-F field list."""
    calls = stub_gh("cat")
    document = '{"query": "mutation { x }", "variables": {"input": {"a": 1}}}'
    r = gh.client.graphql("", input_text=document)
    assert r.stdout == document
    said = calls.read_text()
    assert "--input -" in said
    assert "query=" not in said


def test_graphql_omits_a_none_variable_rather_than_sending_the_word(stub_gh):
    """A None cursor means the first page, not the literal string "None".

    The f-string that builds each -F has no opinion about None, so before this
    the opening page of every paged query went out as `after: "None"` and came
    back INVALID_CURSOR_ARGUMENTS. Two retro scans ran entirely on their REST
    fallback because of it, and the fallback answers, so nothing failed loudly.
    """
    calls = stub_gh("echo '{}'")
    gh.client.graphql(
        "query($cursor: String) { x }",
        variables={"owner": "o", "name": "r", "cursor": None},
    )
    said = calls.read_text()
    # Not a bare "cursor" check: the query text declares $cursor either way.
    assert "-F cursor=" not in said
    assert "None" not in said
    assert "-F owner=o" in said
    assert "-F name=r" in said


def test_graphql_still_sends_a_cursor_that_has_a_value(stub_gh):
    """Omitting None must not also drop the second page's real cursor."""
    calls = stub_gh("echo '{}'")
    gh.client.graphql(
        "query($cursor: String) { x }", variables={"cursor": "Y3Vyc29yOnYyOpHOAA"},
    )
    assert "-F cursor=Y3Vyc29yOnYyOpHOAA" in calls.read_text()


def test_graphql_sends_a_falsy_variable_that_is_not_none(stub_gh):
    """Only None is absent — 0, False and "" are values a query may mean."""
    calls = stub_gh("echo '{}'")
    gh.client.graphql("query { x }", variables={"pr": 0, "draft": False, "q": ""})
    said = calls.read_text()
    assert "-F pr=0" in said
    assert "-F draft=False" in said
    assert "-F q=" in said


def test_a_first_page_read_sends_no_cursor_to_gh(stub_gh):
    """The paging callers now depend on the helper, so test through them.

    `_threads_page` used to carry its own `if cursor:` guard and dropped it
    once the helper omitted None centrally. Every test above this one asserts
    on `graphql()` directly, and `pr_data_test.py` patches `gh.client.graphql`
    outright — so with the helper's omission removed, the real first-page read
    would send `endCursor=None` to gh and nothing would fail. This is the one
    test that watches the argv a paging caller actually produces.
    """
    calls = stub_gh("echo '{\"data\": {\"repository\": {\"pullRequest\": null}}}'")
    gh.pr_reads.fetch_review_threads("owner/repo", 7)
    said = calls.read_text()
    # The query text declares $endCursor either way, so assert on the fields
    # gh was handed, not on the whole command line.
    assert "-F endCursor=" not in said
    assert "-F owner=owner" in said


# ── Reads ───────────────────────────────────────────────────────────────────


def test_pr_view_asks_for_the_fields_as_one_comma_list(stub_gh):
    calls = stub_gh("echo '{\"title\": \"t\", \"body\": \"b\"}'")
    assert gh.client.pr_view(7, "title", "body", repo="o/r") == {"title": "t", "body": "b"}
    assert "pr view 7 --repo o/r --json title,body" in calls.read_text()


def test_pr_view_without_a_number_asks_about_the_current_branch(stub_gh):
    calls = stub_gh("echo '{\"number\": 3}'")
    assert gh.client.pr_view("", "number") == {"number": 3}
    assert "pr view --json number" in calls.read_text()


def test_pr_view_is_empty_when_gh_cannot_answer(stub_gh):
    stub_gh("exit 1")
    assert gh.client.pr_view(7, "title", repo="o/r") == {}


def test_pr_view_is_empty_rather_than_none_on_a_null_body(stub_gh):
    """`gh pr view` answers `null` for a PR it can see but cannot describe."""
    stub_gh("echo null")
    assert gh.client.pr_view(7, "title", repo="o/r") == {}


def test_login_reads_the_authenticated_user(stub_gh):
    calls = stub_gh("echo octocat")
    assert gh.client.login() == "octocat"
    assert "api user --jq .login" in calls.read_text()


def test_login_is_empty_when_gh_is_unauthenticated(stub_gh, no_sleep):
    """Unauthenticated is an answer, so it comes back without a wait."""
    stub_gh("echo 'gh auth login required' >&2; exit 1")
    assert gh.client.login() == ""
    assert no_sleep == []


def test_login_waits_out_a_throttle(stub_gh, no_sleep):
    """It resolves against the API, so it earns the ladder every read gets."""
    stub_gh("""
echo 'You have exceeded a secondary rate limit'
exit 1
""")
    assert gh.client.login() == ""
    assert len(no_sleep) == gh.client.RATE_LIMIT_LADDER.attempts - 1


def test_repo_slug_reads_owner_and_name(stub_gh):
    stub_gh("echo otto-nation/otto-workbench")
    assert gh.client.repo_slug() == "otto-nation/otto-workbench"


def test_repo_slug_is_empty_outside_a_repo(stub_gh, no_sleep):
    """Not a GitHub repository is an answer too — no ladder, no wait."""
    stub_gh("echo 'no git remote found' >&2; exit 1")
    assert gh.client.repo_slug() == ""
    assert no_sleep == []


def test_repo_slug_waits_out_a_throttle(stub_gh, no_sleep):
    """A throttle must not be reported as "not a GitHub repository"."""
    stub_gh("""
echo 'You have exceeded a secondary rate limit'
exit 1
""")
    assert gh.client.repo_slug() == ""
    assert len(no_sleep) == gh.client.RATE_LIMIT_LADDER.attempts - 1
