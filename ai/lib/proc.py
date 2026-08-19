"""One type for what a subprocess said, and one helper for running it.

`ai/` hand-rolls `subprocess.run(..., capture_output=True, text=True)` in
dozens of places, and each wrapper decides for itself what to keep. The ones
that return a positional tuple have no slot for stderr, so the cause of a
failure is gone before any caller can render it — `_gh_api` returning
`(returncode, stdout)` is why a 5xx from `gh` used to surface as an empty
message. Widening the tuple is not the fix: a caller reading fields by
name keeps working when a fourth thing needs carrying, and does not have to
learn the order.

Named `proc` rather than `cmd`: `ai/lib` goes on `sys.path` ahead of the
standard library, and a module called `cmd` there would shadow the stdlib
`cmd` that `pdb` imports.

Stdlib only, deliberately. This is the module everything else in `ai/lib`
should be free to depend on, and pulling in `log`, `ai_usage`, or
`workbench_paths` from here would make that impossible.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# gh reports a transport failure as "HTTP 503: ..." on stderr, whether it came
# from REST or GraphQL, and git over https reports one as "HTTP 502" too.
# Matching the status line is enough to tell a server outage from a local
# misconfiguration.
_SERVER_ERROR_RE = re.compile(r"\bHTTP 5\d\d\b")

# How much of a stream to quote when it is the only account of a failure there
# is. stdout is a payload rather than a sentence, so it needs a bound; stderr is
# quoted whole because a command that writes an essay there is the exception.
DETAIL_LIMIT = 200


@dataclass(frozen=True)
class CmdResult:
    """What a command exited with and what it wrote to each stream.

    Every field defaults, so a test or a short-circuit can build one without
    running anything.
    """

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        """The command exited cleanly."""
        return self.returncode == 0

    @property
    def detail(self) -> str:
        """What the command said about a failure, folded onto one line.

        stderr, because that is where a command explains itself; stdout is the
        answer it was asked for. `combined_output` is the one to match patterns
        against.
        """
        return " ".join((self.stderr or "").split())

    @property
    def combined_output(self) -> str:
        """Both streams, for classifying a failure by what it said.

        Which stream carries the cause depends on the failure. `gh api` writes
        an API error body to stdout and its own status line to stderr, so a 404
        is legible from stdout alone while a 503 or a dropped connection leaves
        stdout empty and says everything on stderr. Reading one of them is how
        a transport failure classified as an unknown error with nothing to
        print.
        """
        return "\n".join(part for part in (self.stdout, self.stderr) if part)

    @property
    def server_error(self) -> bool:
        """The failure was the far end's, so the answer is to wait and retry."""
        return bool(_SERVER_ERROR_RE.search(self.combined_output))


def failure_message(action: str, r: CmdResult | subprocess.CompletedProcess) -> str:
    """Error text for a failed command, quoting what the command said.

    Asserting a cause the code has not established — "cannot determine the repo
    from the git remote" — sends the operator to the wrong place whenever the
    real fault is auth, the network, or a GitHub outage. So name the action that
    failed and let the command's own stderr name the cause. A 5xx is called out
    separately because it is the one case where the answer is to wait rather
    than to change anything.

    Accepts a raw `CompletedProcess` too: most of `ai/` still calls
    `subprocess.run` directly, and those call sites should not have to convert
    before they can report a failure.
    """
    if not isinstance(r, CmdResult):
        r = CmdResult(r.returncode, r.stdout or "", r.stderr or "")
    if r.server_error:
        # `server_error` reads both streams, so this branch is reached by a 5xx
        # that arrived on stdout with nothing on stderr. Quote stdout in that
        # case rather than annotate a retry and then say nothing about it.
        detail = r.detail or " ".join(r.stdout.split())[:DETAIL_LIMIT]
        return f"{action} — server error, retry later: {detail}"
    if not r.detail:
        return action
    return f"{action}: {r.detail}"


def run(
    cmd: list[str],
    *,
    cwd: str | Path | None = None,
    timeout: float | None = None,
    input_text: str | None = None,
) -> CmdResult:
    """Run *cmd*, capturing both streams, and return what it said.

    Never raises on a non-zero exit — the exit code is one of the three things
    the caller gets back, not an exception. `subprocess.TimeoutExpired` still
    propagates: a command that never returned said nothing, and a caller that
    set a timeout is the one that has to decide what that means.
    """
    r = subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout, input=input_text,
    )
    return CmdResult(returncode=r.returncode, stdout=r.stdout or "", stderr=r.stderr or "")
