"""One way to run gh, and the reads every caller was hand-rolling.

`ai/` invoked `gh` as a literal argv head in 45 places across 13 files, and the
knowledge of how to do it well was spread so thin that most sites had none of
it. Eight had no timeout at all. Four returned `(exit_code, stdout)`, so the
stderr explaining a 5xx was discarded before any caller could render it. Retry
existed at one site out of forty-five, which is why a secondary rate limit
surfaced everywhere else as "no data" — indistinguishable from an empty result.

The runner is `run`, and `out`, `ok`, `lines` and `json_out` are the shapes
callers actually wanted from it. `api` and `graphql` sit above them for the
`gh api` surface, which is most of the traffic. Below all of it are the reads
that appeared at two or more call sites; a read used once belongs at its call
site, spelled out with `run`.

Retry is a property of talking to the API, so it lives with the calls that do:
`api`, `graphql`, and the reads above that resolve against GitHub rather than
against a local checkout. A caller driving an artifact download or reading
gh's own configuration gets no ladder, and should not.

The publishing gate is deliberately not here. `pr_comments` gates its writes on
`publishing.enabled()` at the call site and keeps doing so — a second implicit
gate inside the transport would make a policy decision invisible to the code
that owns it.

Like `git_client`, this depends on `log` as well as `proc`, and for more of its
surface than that one does: a rate-limit ladder that waits five minutes in
silence reads as a hang, so the waiting is announced. Whether a *failed* call is
worth logging remains the caller's decision, as it is there.
"""

# doc-group: platform

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Any, Callable

import log
import proc
import timeouts
from proc import CmdResult

# What `run` reports when `gh` is not installed. `proc.run` lets
# `FileNotFoundError` escape, and three call sites caught it while forty-two
# did not; catching it once turns a missing gh into the same degraded answer
# every caller already handles for a non-zero exit.
#
# Deliberately not pushed down into `proc.run`: git being absent is a broken
# machine and should crash where it happens, whereas gh is optional tooling
# that much of `ai/` already treats as best-effort.
GH_MISSING_RETURNCODE = proc.MISSING_RETURNCODE

# Flags whose response size is the data's rather than one round trip's.
# `--paginate` walks as many requests as the result set needs, and
# `--log-failed` pulls a CI job's whole log bundle.
_TRANSFER_FLAGS = frozenset({"--paginate", "--log-failed"})

# Subcommands in the same position: an artifact is as large as the job made it.
_TRANSFER_COMMANDS = frozenset({("run", "download")})

# `gh api repos/<repo>/actions/jobs/<id>/logs` returns the raw log, which is a
# transfer rather than a metadata read however short the endpoint looks.
_TRANSFER_ENDPOINT_SUFFIX = "/logs"


class LineResolutionError(Exception):
    """GitHub cannot resolve line positions for inline comments.

    Raised rather than returned because it is not retryable and not a transport
    failure: the request was understood and the diff moved underneath it, so
    the caller has to re-anchor rather than try again.
    """


# ── Retry policy ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Ladder:
    """How long to wait before each re-attempt, and how many to make."""

    attempts: int
    first_wait: float
    factor: float
    max_wait: float

    def wait(self, attempt: int) -> float:
        """Seconds to sleep after *attempt* (0-based) failed."""
        return min(self.first_wait * (self.factor ** attempt), self.max_wait)


# A secondary rate limit is GitHub telling us to come back later, and its own
# guidance is to wait minutes rather than seconds. Unchanged from the ladder in
# `review_github` that this replaces — the numbers were already right for the
# one case that had them.
RATE_LIMIT_LADDER = _Ladder(attempts=5, first_wait=60.0, factor=1.5, max_wait=300.0)

# A 5xx or a stalled socket is usually over in seconds, so this ladder is short
# enough that a caller in front of a user does not appear wedged.
TRANSIENT_LADDER = _Ladder(attempts=3, first_wait=2.0, factor=2.0, max_wait=8.0)

_MAX_ATTEMPTS = max(RATE_LIMIT_LADDER.attempts, TRANSIENT_LADDER.attempts)

# How GitHub words the throttles that are worth waiting out. A primary rate
# limit reports itself as a 403 with one of these in the body, which is why the
# bare "forbidden" is here alongside the specific ones.
_RATE_LIMIT_MARKERS = (
    "secondary rate limit",
    '"message": "forbidden"',
    "abuse detection",
    "retry later",
)


def _is_rate_limited(said: str) -> bool:
    """Whether the response is a throttle rather than a refusal."""
    lower = said.lower()
    return any(marker in lower for marker in _RATE_LIMIT_MARKERS)


def _is_line_resolution_error(said: str) -> bool:
    """Whether GitHub rejected an inline comment's line position."""
    return "line could not be resolved" in said.lower()


def _ladder_for(r: CmdResult) -> _Ladder | None:
    """Which ladder this failure earns, or None to return it as the answer.

    Every 4xx that is not a throttle lands here as None, and that is the point.
    The ladder being replaced retried any non-zero exit five times with a flat
    five-second delay, so a routine 404 — a branch with no PR yet, an issue
    that was deleted — cost twenty seconds and reported the same empty result
    at the end of it. A 4xx is an answer.
    """
    if r.ok:
        return None
    if _is_rate_limited(r.combined_output):
        return RATE_LIMIT_LADDER
    if r.server_error or r.returncode == proc.TIMEOUT_RETURNCODE:
        return TRANSIENT_LADDER
    return None


def _error_message(r: CmdResult) -> str:
    """The most specific account of a failed call the response supports.

    GitHub's error body is the best of the three, when there is one: it names
    the field that was rejected. Falling back to stderr rather than to a slice
    of an empty stdout is the difference between naming HTTP 503 and printing
    nothing after the colon.
    """
    try:
        parsed = json.loads(r.stdout)
        message = parsed.get("message", "")
        errors = parsed.get("errors", [])
    except (json.JSONDecodeError, AttributeError):
        message, errors = "", []
    if not message:
        return (r.detail or r.combined_output.strip())[:proc.DETAIL_LIMIT]
    if errors:
        message += " — " + "; ".join(str(e) for e in errors)
    return message


def _with_retries(attempt_call: Callable[[], CmdResult], label: str) -> CmdResult:
    """Call *attempt_call* until it succeeds, is unretryable, or runs out.

    *label* names the request in the waiting message, since a five-minute
    silence needs to say what it is waiting for.
    """
    r = CmdResult(returncode=1, stderr=f"{label} was never attempted")
    for attempt in range(_MAX_ATTEMPTS):
        r = attempt_call()
        if r.ok:
            return r
        if _is_line_resolution_error(r.combined_output):
            raise LineResolutionError(r.combined_output[:proc.DETAIL_LIMIT])
        ladder = _ladder_for(r)
        if ladder is None or attempt >= ladder.attempts - 1:
            return r
        wait = ladder.wait(attempt)
        cause = "Rate limited" if ladder is RATE_LIMIT_LADDER else "Transient failure"
        log.warn(
            f"{cause} on {label} (attempt {attempt + 1}/{ladder.attempts}), "
            f"waiting {wait:g}s: {_error_message(r)}"
        )
        sleep(wait)
    return r


# ── Runner ──────────────────────────────────────────────────────────────────


def _timeout_for(args: tuple[str, ...]) -> float | None:
    """The bound for this invocation — see `timeouts` for the tiers.

    Keyed on the argv rather than left to the caller, for the reason
    `timeouts` gives: one `gh api` round trip used to be 30s in
    `review_github`, 10s in `pr-rebase` and 10s in `retro-scan`, and no
    principle separated those numbers.
    """
    if _TRANSFER_FLAGS.intersection(args):
        return timeouts.TRANSFER
    if args[:2] in _TRANSFER_COMMANDS:
        return timeouts.TRANSFER
    if args[:1] == ("api",) and len(args) > 1 and args[1].endswith(_TRANSFER_ENDPOINT_SUFFIX):
        return timeouts.TRANSFER
    return timeouts.NETWORK


def run(
    *args: str,
    cwd: str | Path | None = None,
    input_text: str | None = None,
) -> CmdResult:
    """Run gh with *args* in *cwd*, capturing both streams.

    Never raises on a non-zero exit, and never raises when gh is missing — both
    arrive as a `CmdResult` the caller reads the same way. A timeout arrives
    that way too, carrying `proc.TIMEOUT_RETURNCODE`.

    There is no `timeout` parameter and no retry here. The bound follows from
    the argv, and retry belongs to `api` and `graphql`, which know they are
    talking to the API rather than driving a local artifact download.
    """
    try:
        return proc.run(
            ["gh", *args], cwd=cwd, timeout=_timeout_for(args), input_text=input_text,
        )
    except FileNotFoundError:
        return CmdResult(
            returncode=GH_MISSING_RETURNCODE,
            stderr="gh is not installed — install the GitHub CLI to use this",
        )


def out(
    *args: str,
    cwd: str | Path | None = None,
    default: str = "",
) -> str:
    """Stripped stdout, or *default* when gh exited non-zero."""
    r = run(*args, cwd=cwd)
    return r.stdout.strip() if r.ok else default


def ok(*args: str, cwd: str | Path | None = None) -> bool:
    """Whether gh exited zero."""
    return run(*args, cwd=cwd).ok


def lines(*args: str, cwd: str | Path | None = None) -> list[str]:
    """Stdout split into non-empty lines, or empty when gh exited non-zero."""
    return [line for line in out(*args, cwd=cwd).splitlines() if line]


def json_out(*args: str, cwd: str | Path | None = None, default: Any = None) -> Any:
    """Stdout parsed as JSON, or *default* when gh failed or said nothing usable.

    Unparseable output and a failed call are the same answer deliberately: gh
    writes an HTML error page or an empty string in both cases, and no caller
    here distinguishes them.
    """
    r = run(*args, cwd=cwd)
    if not r.ok:
        return default
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        return default


# ── API ─────────────────────────────────────────────────────────────────────


def _api_argv(
    endpoint: str,
    method: str,
    jq: str,
    paginate: bool,
    slurp: bool,
    headers: dict[str, str] | None,
    fields: dict[str, str] | None,
    raw_fields: dict[str, str] | None,
    body_on_stdin: bool = False,
    allow_escape_sequences: bool = False,
) -> tuple[str, ...]:
    """The full `gh api` argv, in the order gh documents.

    *body_on_stdin* adds `--input -`. gh ignores whatever is on stdin without
    it, so a caller that passes a body and forgets the flag sends an empty
    request and reads GitHub's complaint about the missing field as a bug in
    the payload it built.

    *allow_escape_sequences* adds `--allow-escape-sequences`. Without it gh
    refuses to print a response containing terminal escapes and exits 1, which
    a caller reading only stdout sees as an empty body rather than as a
    refusal — the shape a log endpoint hits whenever the job coloured its
    output.
    """
    argv = ["api", endpoint]
    if method != "GET":
        argv += ["--method", method]
    if body_on_stdin:
        argv += ["--input", "-"]
    if allow_escape_sequences:
        argv.append("--allow-escape-sequences")
    if paginate:
        argv.append("--paginate")
    if slurp:
        argv.append("--slurp")
    if jq:
        argv += ["--jq", jq]
    for key, value in (headers or {}).items():
        argv += ["--header", f"{key}: {value}"]
    for key, value in (raw_fields or {}).items():
        argv += ["-f", f"{key}={value}"]
    for key, value in (fields or {}).items():
        argv += ["-F", f"{key}={value}"]
    return tuple(argv)


def api(
    endpoint: str,
    *,
    method: str = "GET",
    jq: str = "",
    paginate: bool = False,
    slurp: bool = False,
    headers: dict[str, str] | None = None,
    fields: dict[str, str] | None = None,
    raw_fields: dict[str, str] | None = None,
    input_text: str | None = None,
    retry: bool = True,
    allow_escape_sequences: bool = False,
) -> CmdResult:
    """One `gh api` call, retried when waiting is the remedy.

    *fields* are passed as `-F`, so gh types integers and booleans; *raw_fields*
    are passed as `-f` and stay strings. *input_text* is fed to gh on stdin as
    the whole request body, which is how a JSON document too nested for a field
    list is sent — the callers that used to write it to a temporary file first.
    *allow_escape_sequences* lets a response carrying terminal escapes through
    instead of gh refusing to print it, which only a log endpoint needs.

    Retry is on by default because rate limiting is a property of the API
    rather than of any one caller. Pass ``retry=False`` where a second attempt
    would duplicate a side effect that the first one already had.
    """
    argv = _api_argv(
        endpoint, method, jq, paginate, slurp, headers, fields, raw_fields,
        body_on_stdin=input_text is not None,
        allow_escape_sequences=allow_escape_sequences,
    )
    call = functools.partial(run, *argv, input_text=input_text)
    return _with_retries(call, f"{method} {endpoint}") if retry else call()


def api_json(endpoint: str, *, default: Any = None, **kwargs: Any) -> Any:
    """`api` with its stdout parsed, or *default* when it failed or was unparseable."""
    r = api(endpoint, **kwargs)
    if not r.ok:
        return default
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        return default


def graphql(
    query: str,
    *,
    variables: dict[str, Any] | None = None,
    input_text: str | None = None,
    retry: bool = True,
) -> CmdResult:
    """One GraphQL call, retried on the same terms as `api`.

    The query rides as a raw field (`-f`) and every variable as a typed one
    (`-F`), so gh detects integers and booleans. *input_text* sends a whole
    query document on stdin instead, which is what a mutation with a nested
    variable needs.
    """
    argv: list[str] = ["api", "graphql"]
    if input_text is not None:
        argv += ["--input", "-"]
    else:
        argv += ["-f", f"query={query}"]
    for key, value in (variables or {}).items():
        argv += ["-F", f"{key}={value}"]
    call = functools.partial(run, *argv, input_text=input_text)
    return _with_retries(call, "graphql") if retry else call()


# ── Reads ───────────────────────────────────────────────────────────────────


def pr_view(
    pr: str | int,
    *fields: str,
    repo: str = "",
    cwd: str | Path | None = None,
) -> dict:
    """The named *fields* of a PR, as a dict, or empty when gh cannot answer.

    *pr* may be a number, a URL, or a branch name — whatever `gh pr view`
    accepts — and an empty string asks for the PR of the current branch.
    *fields* are gh's JSON field names, one per argument.

    Twelve call sites asked this question and eight of them post-processed the
    answer with a `--jq` expression, so the field set and the extraction had to
    stay in step across two strings in different languages. Returning the dict
    puts the extraction in Python, where the caller can read a key by name.
    """
    argv = ["pr", "view"]
    if pr != "":
        argv.append(str(pr))
    if repo:
        argv += ["--repo", repo]
    argv += ["--json", ",".join(fields)]
    return json_out(*argv, cwd=cwd, default={}) or {}


def login() -> str:
    """The authenticated user's GitHub login, or "" when gh cannot say.

    Takes no *cwd*: `gh api user` asks who the token belongs to, which no
    repository can change the answer to.
    """
    r = api("user", jq=".login")
    return r.stdout.strip() if r.ok else ""


def repo_slug(cwd: str | Path | None = None) -> str:
    """``owner/repo`` for the repository at *cwd*, or "" when gh cannot say.

    The failure is the caller's to report: `pr_context.detect_repo` exits on it
    with the command quoted, while the review scripts fall back to a flag. It
    is worth retrying first, though — this resolves against the API like `api`
    does, and reporting "not a GitHub repository" because of a throttle sends
    the caller after a fault that is not there.
    """
    call = functools.partial(
        run, "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner", cwd=cwd,
    )
    r = _with_retries(call, "repo view")
    return r.stdout.strip() if r.ok else ""
