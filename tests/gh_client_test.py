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
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import gh_client  # noqa: E402
import proc  # noqa: E402
import timeouts  # noqa: E402
from proc import CmdResult  # noqa: E402


def _stub_gh(tmp_path: Path, monkeypatch, body: str) -> Path:
    """Put a `gh` on PATH whose body is *body*, and record every invocation.

    The stub appends its argv to `calls.txt` before running *body*, so a test
    can assert what the client actually asked for as well as what it did with
    the answer.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls = tmp_path / "calls.txt"
    script = bin_dir / "gh"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {calls}\n'
        f"{body}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return calls


@pytest.fixture
def no_sleep(monkeypatch) -> list[float]:
    """Collect the ladder's waits instead of serving them.

    The client holds its own `sleep` name so this replaces only the waits it
    asks for. Patching `time.sleep` itself would also catch the millisecond
    polling `subprocess` does while waiting on a bounded child, which lands a
    stray 0.001 in the middle of the ladder.
    """
    slept: list[float] = []
    monkeypatch.setattr(gh_client, "sleep", slept.append)
    return slept


# ── Timeout policy ──────────────────────────────────────────────────────────


def test_a_single_round_trip_takes_the_network_tier():
    assert gh_client._timeout_for(("api", "user")) == timeouts.NETWORK


def test_pagination_takes_the_transfer_tier():
    """--paginate walks as many requests as the result set needs."""
    assert gh_client._timeout_for(("api", "--paginate", "repos/o/r/pulls")) == timeouts.TRANSFER


def test_an_artifact_download_takes_the_transfer_tier():
    assert gh_client._timeout_for(("run", "download", "42")) == timeouts.TRANSFER


def test_a_failed_log_bundle_takes_the_transfer_tier():
    assert gh_client._timeout_for(("run", "view", "42", "--log-failed")) == timeouts.TRANSFER


def test_a_job_log_endpoint_takes_the_transfer_tier():
    """Short endpoint, whole log in the body."""
    assert gh_client._timeout_for(
        ("api", "repos/o/r/actions/jobs/7/logs"),
    ) == timeouts.TRANSFER


def test_a_run_view_without_logs_stays_on_the_network_tier():
    assert gh_client._timeout_for(("run", "view", "42", "--json", "jobs")) == timeouts.NETWORK


def test_an_empty_argv_still_resolves_a_tier():
    assert gh_client._timeout_for(()) == timeouts.NETWORK


def test_run_takes_no_timeout_from_its_caller():
    """The bound is the client's to decide, so there is nothing to override."""
    with pytest.raises(TypeError):
        gh_client.run("api", "user", timeout=1)


# ── Retry classification ────────────────────────────────────────────────────


def test_a_success_earns_no_ladder():
    assert gh_client._ladder_for(CmdResult()) is None


@pytest.mark.parametrize("said", [
    "You have exceeded a secondary rate limit",
    '{"message": "Forbidden"}',
    "triggered an abuse detection mechanism",
    "Please retry later",
])
def test_a_throttle_earns_the_rate_limit_ladder(said):
    r = CmdResult(returncode=1, stdout=said)
    assert gh_client._ladder_for(r) is gh_client.RATE_LIMIT_LADDER


def test_a_server_error_earns_the_transient_ladder():
    r = CmdResult(returncode=1, stderr="HTTP 503: Service Unavailable")
    assert gh_client._ladder_for(r) is gh_client.TRANSIENT_LADDER


def test_a_timeout_earns_the_transient_ladder():
    r = CmdResult(returncode=proc.TIMEOUT_RETURNCODE, stderr="timed out after 30s")
    assert gh_client._ladder_for(r) is gh_client.TRANSIENT_LADDER


def test_a_not_found_earns_no_ladder():
    """A 4xx is an answer, not a failure.

    The ladder this replaces retried any non-zero exit five times with a flat
    five-second delay, so a branch with no PR yet cost twenty seconds to learn
    it had no PR.
    """
    r = CmdResult(returncode=1, stdout='{"message": "Not Found"}')
    assert gh_client._ladder_for(r) is None


def test_the_rate_limit_ladder_backs_off_and_caps():
    ladder = gh_client.RATE_LIMIT_LADDER
    waits = [ladder.wait(n) for n in range(ladder.attempts)]
    assert waits == sorted(waits)
    assert waits[0] == 60.0
    assert max(waits) <= ladder.max_wait


def test_the_transient_ladder_is_short_enough_not_to_look_wedged():
    ladder = gh_client.TRANSIENT_LADDER
    assert sum(ladder.wait(n) for n in range(ladder.attempts - 1)) <= 10.0


# ── Retry loop ──────────────────────────────────────────────────────────────


def test_a_throttle_is_retried_until_it_clears(tmp_path, monkeypatch, no_sleep):
    """The stub 403s twice, then answers."""
    counter = tmp_path / "n"
    _stub_gh(tmp_path, monkeypatch, f"""
n=$(cat {counter} 2>/dev/null || echo 0)
echo $((n + 1)) > {counter}
if [ "$n" -lt 2 ]; then
  echo '{{"message": "Forbidden"}}'
  exit 1
fi
echo '{{"login": "octocat"}}'
""")
    r = gh_client.api("user")
    assert r.ok
    assert json.loads(r.stdout)["login"] == "octocat"
    assert len(no_sleep) == 2


def test_a_not_found_is_returned_on_the_first_attempt(tmp_path, monkeypatch, no_sleep):
    calls = _stub_gh(tmp_path, monkeypatch, """
echo '{"message": "Not Found"}'
exit 1
""")
    r = gh_client.api("repos/o/r/pulls/9999")
    assert not r.ok
    assert calls.read_text().count("\n") == 1
    assert no_sleep == []


def test_a_throttle_that_never_clears_gives_up_and_returns_it(tmp_path, monkeypatch, no_sleep):
    _stub_gh(tmp_path, monkeypatch, """
echo 'You have exceeded a secondary rate limit'
exit 1
""")
    r = gh_client.api("user")
    assert not r.ok
    assert len(no_sleep) == gh_client.RATE_LIMIT_LADDER.attempts - 1


def test_retry_off_makes_exactly_one_attempt(tmp_path, monkeypatch, no_sleep):
    calls = _stub_gh(tmp_path, monkeypatch, """
echo 'You have exceeded a secondary rate limit'
exit 1
""")
    assert not gh_client.api("user", retry=False).ok
    assert calls.read_text().count("\n") == 1
    assert no_sleep == []


def test_an_unresolvable_line_raises_rather_than_retrying(tmp_path, monkeypatch, no_sleep):
    """Not a transport failure: the diff moved, so the caller must re-anchor."""
    _stub_gh(tmp_path, monkeypatch, """
echo '{"message": "line could not be resolved to a diff position"}'
exit 1
""")
    with pytest.raises(gh_client.LineResolutionError):
        gh_client.api("repos/o/r/pulls/1/comments", method="POST")
    assert no_sleep == []


# ── Runner ──────────────────────────────────────────────────────────────────


def test_a_missing_gh_is_a_result_rather_than_an_exception(tmp_path, monkeypatch):
    """Three call sites caught FileNotFoundError and forty-two did not."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    r = gh_client.run("api", "user")
    assert r.returncode == gh_client.GH_MISSING_RETURNCODE
    assert "not installed" in r.stderr
    assert gh_client.out("api", "user", default="unknown") == "unknown"


def test_run_carries_stderr_so_a_caller_can_name_the_cause(tmp_path, monkeypatch):
    _stub_gh(tmp_path, monkeypatch, "echo 'HTTP 503: upstream is down' >&2; exit 1")
    r = gh_client.run("api", "user")
    assert not r.ok
    assert "503" in r.detail
    assert r.server_error


def test_out_strips_and_returns_stdout(tmp_path, monkeypatch):
    _stub_gh(tmp_path, monkeypatch, "echo '  octocat  '")
    assert gh_client.out("api", "user") == "octocat"


def test_ok_reads_the_exit_code(tmp_path, monkeypatch):
    _stub_gh(tmp_path, monkeypatch, "exit 0")
    assert gh_client.ok("auth", "status")


def test_lines_drops_blanks(tmp_path, monkeypatch):
    _stub_gh(tmp_path, monkeypatch, "printf 'a\\n\\nb\\n'")
    assert gh_client.lines("api", "user") == ["a", "b"]


def test_json_out_falls_back_when_the_output_is_not_json(tmp_path, monkeypatch):
    _stub_gh(tmp_path, monkeypatch, "echo '<html>gateway timeout</html>'")
    assert gh_client.json_out("api", "user", default={"x": 1}) == {"x": 1}


def test_json_out_falls_back_on_a_failed_call(tmp_path, monkeypatch, no_sleep):
    _stub_gh(tmp_path, monkeypatch, "exit 1")
    assert gh_client.json_out("api", "user", default=[]) == []


def test_stdin_reaches_gh(tmp_path, monkeypatch):
    _stub_gh(tmp_path, monkeypatch, "cat")
    r = gh_client.run("api", "graphql", input_text='{"query": "x"}')
    assert r.stdout == '{"query": "x"}'


# ── API argv ────────────────────────────────────────────────────────────────


def test_a_get_is_just_the_endpoint():
    assert gh_client._api_argv(
        "user", "GET", "", False, False, None, None, None,
    ) == ("api", "user")


def test_a_write_names_its_method():
    assert gh_client._api_argv(
        "repos/o/r/issues", "POST", "", False, False, None, None, None,
    ) == ("api", "repos/o/r/issues", "--method", "POST")


def test_typed_and_raw_fields_use_different_flags():
    """-F lets gh detect an integer; -f keeps it a string."""
    argv = gh_client._api_argv(
        "graphql", "GET", "", False, False, None, {"number": "7"}, {"query": "q"},
    )
    assert "-f" in argv and "query=q" in argv
    assert "-F" in argv and "number=7" in argv


def test_headers_are_passed_one_per_flag():
    argv = gh_client._api_argv(
        "repos/o/r/pulls/1", "GET", "", False, False,
        {"Accept": "application/vnd.github.v3.diff"}, None, None,
    )
    assert "--header" in argv
    assert "Accept: application/vnd.github.v3.diff" in argv


def test_paginate_and_slurp_both_reach_the_argv():
    argv = gh_client._api_argv(
        "repos/o/r/pulls/1/comments", "GET", "", True, True, None, None, None,
    )
    assert "--paginate" in argv and "--slurp" in argv


def test_a_jq_expression_reaches_the_argv():
    argv = gh_client._api_argv("user", "GET", ".login", False, False, None, None, None)
    assert argv[-2:] == ("--jq", ".login")


def test_a_body_on_stdin_names_itself_in_the_argv():
    """gh ignores stdin without `--input -`, and sends an empty body instead."""
    argv = gh_client._api_argv(
        "repos/o/r/pulls/1/reviews", "POST", "", False, False, None, None, None,
        body_on_stdin=True,
    )
    assert argv == ("api", "repos/o/r/pulls/1/reviews", "--method", "POST", "--input", "-")


# ── Request bodies ──────────────────────────────────────────────────────────


def test_api_sends_its_body_on_stdin(tmp_path, monkeypatch):
    calls = _stub_gh(tmp_path, monkeypatch, "cat")
    r = gh_client.api(
        "repos/o/r/pulls/1/reviews", method="POST", input_text='{"event": "COMMENT"}',
    )
    assert r.stdout == '{"event": "COMMENT"}'
    assert "--input -" in calls.read_text()


def test_api_without_a_body_asks_gh_to_read_nothing(tmp_path, monkeypatch):
    calls = _stub_gh(tmp_path, monkeypatch, "echo '{}'")
    gh_client.api("user")
    assert "--input" not in calls.read_text()


def test_graphql_sends_a_whole_document_on_stdin(tmp_path, monkeypatch):
    """A mutation with a nested variable does not fit gh's -f/-F field list."""
    calls = _stub_gh(tmp_path, monkeypatch, "cat")
    document = '{"query": "mutation { x }", "variables": {"input": {"a": 1}}}'
    r = gh_client.graphql("", input_text=document)
    assert r.stdout == document
    said = calls.read_text()
    assert "--input -" in said
    assert "query=" not in said


# ── Reads ───────────────────────────────────────────────────────────────────


def test_pr_view_asks_for_the_fields_as_one_comma_list(tmp_path, monkeypatch):
    calls = _stub_gh(tmp_path, monkeypatch, "echo '{\"title\": \"t\", \"body\": \"b\"}'")
    assert gh_client.pr_view(7, "title", "body", repo="o/r") == {"title": "t", "body": "b"}
    assert "pr view 7 --repo o/r --json title,body" in calls.read_text()


def test_pr_view_without_a_number_asks_about_the_current_branch(tmp_path, monkeypatch):
    calls = _stub_gh(tmp_path, monkeypatch, "echo '{\"number\": 3}'")
    assert gh_client.pr_view("", "number") == {"number": 3}
    assert "pr view --json number" in calls.read_text()


def test_pr_view_is_empty_when_gh_cannot_answer(tmp_path, monkeypatch):
    _stub_gh(tmp_path, monkeypatch, "exit 1")
    assert gh_client.pr_view(7, "title", repo="o/r") == {}


def test_pr_view_is_empty_rather_than_none_on_a_null_body(tmp_path, monkeypatch):
    """`gh pr view` answers `null` for a PR it can see but cannot describe."""
    _stub_gh(tmp_path, monkeypatch, "echo null")
    assert gh_client.pr_view(7, "title", repo="o/r") == {}


def test_login_reads_the_authenticated_user(tmp_path, monkeypatch):
    calls = _stub_gh(tmp_path, monkeypatch, "echo octocat")
    assert gh_client.login() == "octocat"
    assert "api user --jq .login" in calls.read_text()


def test_login_is_empty_when_gh_is_unauthenticated(tmp_path, monkeypatch):
    _stub_gh(tmp_path, monkeypatch, "echo 'gh auth login required' >&2; exit 1")
    assert gh_client.login() == ""


def test_repo_slug_reads_owner_and_name(tmp_path, monkeypatch):
    _stub_gh(tmp_path, monkeypatch, "echo otto-nation/otto-workbench")
    assert gh_client.repo_slug() == "otto-nation/otto-workbench"


def test_repo_slug_is_empty_outside_a_repo(tmp_path, monkeypatch):
    _stub_gh(tmp_path, monkeypatch, "echo 'no git remote found' >&2; exit 1")
    assert gh_client.repo_slug() == ""
