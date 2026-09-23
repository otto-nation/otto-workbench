"""Pi CLI backend for agent.backend.

Implements preflight(), prompt(), invoke_agent(), and invoke_fix() by
building `pi` commands and running them as subprocesses.

invoke_agent and invoke_fix use RPC mode (--mode rpc) for bidirectional control:
  - Budget enforcement via accumulated message_end costs + get_session_stats
  - Clean abort via {"type": "abort"} instead of SIGTERM
  - Claude-compatible result records written to session logs
  - A response with success:false on prompt or parse ends the run and is
    reported, instead of being waited out

prompt() uses print mode (pi -p) for simplicity.

Tool allowlists:
  --tools is a membership filter over every registered tool, built-in and
  extension alike, so naming a tool this machine does not have costs nothing and
  nothing here mirrors the package reachability check in ai/pi/steps.sh — a
  machine that cannot fetch the package simply runs with the built-ins. Agent runs
  get the read-only GitHub tools plus web and Go navigation; fix runs get web and
  Go navigation only, because a fix pass has no GitHub business. The extension's
  gh_pr_reply_comment, gh_pr_bulk_reply and gh_pr_post_comment are in neither
  list: our scripts own what reaches a PR, and naming a tool is the only way the
  allowlist grants it.

Pi CLI reference:
  -p / --print     Prompt mode (non-interactive, like claude -p)
  --mode rpc       Bidirectional JSONL over stdin/stdout
  --approve        Auto-accept project trust (like claude --permission-mode acceptEdits)
  --no-session     Ephemeral mode (don't persist session)
  --tools <list>   Allowlist specific tools
  --model <id>     Model selection
  --thinking <lvl> Thinking depth: off, minimal, low, medium, high, xhigh
  --append-system-prompt <text>  Inject additional system prompt
  --verbose        Verbose output

Gaps vs Claude Code CLI:
  --max-turns      Not available; counted via turn_end events, abort via RPC
  --max-budget-usd Not available; tracked via message_end costs, abort via RPC
  --add-dir        Not available; directories passed in prompt text
  --agent          Not available; use --append-system-prompt with agent file contents
"""

# doc-group: backend

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from agent import usage as ai_usage
from core import log
from core import timeouts
from core.proc import _kill_group
from agent.backend import AgentInvocation, agent_env
from agent.backend_events import (
    _log_stderr_on_failure, parse_pi_cost, parse_pi_event, pi_prompt_result,
    pi_write_tool_used,
)
from core.log import ANSI_DIM, ANSI_RESET, _print_lock

PI_TOOLS = "bash,read,write,edit,grep,find,ls"

# Tools from the shared pi-extensions package, installed globally by ai/pi/steps.sh.
# See the module docstring for why the posting tools are absent from both lists.
PI_GITHUB_TOOLS = (
    "gh_pr_context,gh_pr_diff,gh_pr_unresolved_comments,"
    "gh_ci_failures,gh_merge_queue_failures"
)
PI_RESEARCH_TOOLS = "web_search,web_fetch,docs_index,go_references,go_call_hierarchy"

PI_AGENT_TOOLS = f"{PI_TOOLS},{PI_GITHUB_TOOLS},{PI_RESEARCH_TOOLS}"
PI_FIX_TOOLS = f"{PI_TOOLS},{PI_RESEARCH_TOOLS}"

AGENTS_DIR = Path.home() / ".claude" / "agents"
# Mirrors AGENTS_SKILLS_DIR in lib/constants.sh, where ai/skills/steps.sh installs.
# tests/workbench_roots.bats fails when the two drift.
AGENTS_SKILLS_DIR = Path.home() / ".agents" / "skills"
# extensions-cli/ rather than extensions/: ai/pi/steps.sh installs everything in
# the latter into ~/.pi/agent/extensions, where Pi loads it in every session.
# This one is passed with --extension for review and fix runs only, and gates
# writes on REVIEW_WORKTREE_DIR — a variable no interactive session sets.
REVIEW_EXTENSION = (
    Path(__file__).resolve().parent.parent.parent
    / "pi" / "extensions-cli" / "review-guard.ts"
)
# The names review-guard.ts gates on. It fail-opens when the first is absent, so
# an invocation that forgets them is an ungated session rather than an error.
ENV_REVIEW_WORKTREE_DIR = "REVIEW_WORKTREE_DIR"
ENV_REVIEW_ALLOWED_DIRS = "REVIEW_ALLOWED_DIRS"


def _guard_env(inv: AgentInvocation) -> dict[str, str]:
    """The subprocess environment, with the review guard's roots added.

    Set here rather than in the review pipeline because this is where the
    extension is attached: every caller that gets the guard gets its bounds with
    it, including the fix pass and the eval harness.

    ``inv.env`` is a complete mapping when set, so it is extended rather than
    merged over os.environ — the eval fixtures put recording shims on PATH and
    inheriting the real environment behind them would defeat that.
    """
    env = agent_env(inv)
    env[ENV_REVIEW_WORKTREE_DIR] = inv.cwd
    # The review document lives under ~/.local/state/workbench/reviews/, which is
    # outside the worktree by design. Gating on the worktree alone would refuse
    # the one write every phase is dispatched to make.
    extra = [d for d in inv.add_dirs if d]
    if extra:
        env[ENV_REVIEW_ALLOWED_DIRS] = os.pathsep.join(extra)
    return env


def _read_agent_prompt(agent: str) -> str | None:
    """Read an agent's system prompt from ~/.claude/agents/<name>.md."""
    agent_file = AGENTS_DIR / f"{agent}.md"
    if agent_file.is_file():
        return agent_file.read_text()
    log.warn(f"agent file not found: {agent_file}")
    return None


AGENT_PROTOCOL_PLACEHOLDER = "AGENT_PROTOCOL_PLACEHOLDER"


def _resolve_skill_path(agent: str) -> Path | None:
    """Check if a Pi-format SKILL.md exists for the given agent name.

    Returns None if the file is missing or still contains the unresolved placeholder.
    """
    skill_file = AGENTS_SKILLS_DIR / agent / "SKILL.md"
    if not skill_file.is_file():
        return None
    if AGENT_PROTOCOL_PLACEHOLDER in skill_file.read_text():
        return None
    return skill_file


# ── Command builders ──────────────────────────────────────────────────────────


def _build_prompt_cmd(
    model: str | None = None, provider: str | None = None,
    thinking: str | None = None,
) -> list[str]:
    # --mode json, not bare -p: print mode emits the reply and nothing else, so
    # a prompt measured that way lands no ledger row at all. The Claude backend
    # pairs --print with --output-format for the same reason.
    #
    # --no-tools is what makes the shape match its name. `-p` is only
    # non-interactive, not tool-less: Pi's built-ins stay registered and
    # `--approve` runs them without asking, so a "stateless text-in/text-out"
    # call arrived holding a shell. A conflict resolver used it to run
    # `git rebase --edit-todo`, which opened `vi` on a pipe with no terminal,
    # and `prompt()` is UNBOUNDED — the rebase sat there until the job's own
    # timeout killed it 45 minutes later. Nothing in a prompt's contract wants
    # a tool: the caller sends text and parses text back.
    cmd = ["pi", "-p", "--mode", "json", "--no-session", "--approve", "--no-tools"]
    if provider:
        cmd += ["--provider", provider]
    if model:
        cmd += ["--model", model]
    if thinking:
        cmd += ["--thinking", thinking]
    return cmd


def _build_agent_cmd(inv: AgentInvocation, extension: str | None = None) -> list[str]:
    cmd = [
        "pi", "--mode", "rpc", "--no-session", "--approve", "--verbose",
        "--tools", PI_AGENT_TOOLS,
    ]
    if inv.agent:
        skill_path = _resolve_skill_path(inv.agent)
        if skill_path:
            cmd += ["--skill", str(skill_path)]
        elif (agent_prompt := _read_agent_prompt(inv.agent)) is not None:
            cmd += ["--append-system-prompt", agent_prompt]
        else:
            raise FileNotFoundError(f"Agent file not found: {AGENTS_DIR / f'{inv.agent}.md'}")
    if inv.provider:
        cmd += ["--provider", inv.provider]
    if inv.model:
        cmd += ["--model", inv.model]
    if inv.thinking:
        cmd += ["--thinking", inv.thinking]
    if extension:
        cmd += ["--extension", extension]
    return cmd


def _build_fix_cmd(inv: AgentInvocation, extension: str | None = None) -> list[str]:
    # ceiling: no `gh` deny here, unlike the Claude backend's FIX_DENIED_TOOLS —
    # `--tools` allowlists tool *names*, so barring one bash command is not
    # expressible. A fix agent has no GitHub business and the fix templates say
    # so, but here that is trusted rather than enforced; the outward writes that
    # matter are gated at the write instead (see `publishing`). Upgrade when pi
    # grows per-command bash permissions.
    #
    # PI_GITHUB_TOOLS is withheld for the same reason: naming a tool is how the
    # allowlist grants it, so the one form of GitHub reach this list can express
    # is the one it declines to.
    cmd = [
        "pi", "--mode", "rpc", "--no-session", "--approve", "--verbose",
        "--tools", PI_FIX_TOOLS,
    ]
    if inv.provider:
        cmd += ["--provider", inv.provider]
    if inv.model:
        cmd += ["--model", inv.model]
    if inv.thinking:
        cmd += ["--thinking", inv.thinking]
    if extension:
        cmd += ["--extension", extension]
    return cmd


# ── RPC protocol helpers ─────────────────────────────────────────────────────


def _send(proc: subprocess.Popen, command: dict) -> bool:
    """Write a JSONL command to the RPC process's stdin. True when it landed.

    Writing to a child that has already exited raises rather than failing
    quietly, and every caller here is capable of reaching a dead Pi: the very
    first `prompt` when Pi rejected its own flags and exited, and the mid-run
    `abort`/`steer` that follow a turn_end Pi emitted on its way out. An
    unguarded traceback from those is the same class of fault this module was
    fixed for — an early failure surfacing as something other than the error
    it is — so the failure is reported to the caller, which knows whether the
    command mattered.
    """
    try:
        proc.stdin.write(json.dumps(command) + "\n")
        proc.stdin.flush()
        return True
    except (BrokenPipeError, ValueError):
        return False


def _read_rpc_response(proc: subprocess.Popen, command_type: str) -> dict:
    """Read lines until we get a response for the given command type.

    Skips any interleaved events (shouldn't occur after agent_end,
    but handles gracefully).
    """
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if data.get("type") == "response" and data.get("command") == command_type:
            return data
    return {}


# Pi wraps every RPC reply in {type, command, success, data}. Only these two
# commands make a failure fatal to the run. Per Pi's RPC protocol a `prompt`
# reply with success:false means the prompt was rejected before acceptance, so
# no agent_start, no turn_end and no agent_end will ever follow, and a reader
# waiting for them waits out the whole timeout. `parse` is the same fault one
# step earlier: Pi could not read the command line at all. A failed `steer`,
# `follow_up` or `abort` is not fatal — _check_limits sends those mid-run, the
# agent that rejected one is still streaming, and the run still ends in
# agent_end. Ending the run there would discard the work of a healthy agent
# over a steering message it no longer needed.
_FATAL_RPC_COMMANDS = frozenset({"prompt", "parse"})

# Enough of a provider or auth error to act on. The text is a message meant for
# a human, not a payload, and a runaway one would otherwise land whole in the
# session log and in every ledger row that quotes it.
_RPC_ERROR_MAX_CHARS = 2000

# Pi was gone before its prompt could be written. Reported in the same shape as
# a refusal, because it is the same failure from the caller's side: the run
# never started and nothing downstream should wait for it.
_PROMPT_UNDELIVERED = (
    "pi exited before the prompt could be sent — its stdin was already closed"
)

# What a run Pi refused exits with when Pi's own status does not say so. A
# clean RPC shutdown after a rejected prompt is exit 0, and a caller reading
# only the status would take that for a successful run.
_RPC_ERROR_EXIT_CODE = 1


def _rpc_response_error(data: dict) -> str | None:
    """The error text of an RPC response that ends the run, or None.

    A non-fatal failure is warned about rather than returned: the run is still
    streaming and its work is worth more than the command Pi declined. Nothing
    is dropped in silence either way — the unconditional skip that preceded
    this is what let an auth failure consume a whole timeout with no output.

    ``success`` is compared against False by identity: a response that carries
    no ``success`` key is not a failure, and a truthiness test would read an
    absent key, a 0 or an empty string as one.
    """
    if data.get("success") is not False:
        return None
    command = data.get("command", "")
    detail = str(data.get("error") or f"pi rejected {command or 'a command'}")
    detail = detail[:_RPC_ERROR_MAX_CHARS]
    if command in _FATAL_RPC_COMMANDS:
        return detail
    log.warn(f"pi rpc {command or 'command'} failed: {detail}")
    return None


def _wait_for_exit(proc: subprocess.Popen, *, abandoned: bool) -> bool:
    """Wait for Pi to exit after its stdin was closed. True when it is gone.

    Bounded only for a run this module abandoned. Breaking out of the event
    loop on a fatal response leaves a child that is still alive with an unread
    stdout pipe, and waiting on that forever is the same silent hang the break
    exists to end, one line further down.

    A run that reached agent_end is waited for as long as it takes, which is
    what the code did before there was an error path at all. Pi's shutdown does
    real work — flushing its log, tearing down extensions — and this machine's
    own test runner documents subprocesses losing the scheduler for seconds
    under load. A bound here would kill a healthy run that had already written
    its output and report the whole thing as a failure, which is a worse
    outcome than the wait it would be shortening.

    The whole group is signalled, not the direct child: Pi leads a session of
    its own and the tools it spawned outlive a kill aimed at it alone. A group
    that will not reap even after SIGKILL is reported and then left: raising
    would replace a failure the caller can act on with a traceback none of them
    handles, and there is no further signal to try.

    False is that last case, and it is the whole reason this returns anything.
    A caller that reads the child's stderr afterwards blocks until the child
    closes it, which a process that would not die for SIGKILL never does — so
    "reported and left" becomes a silent hang two lines later unless the caller
    is told to skip it.
    """
    if not abandoned:
        proc.wait(timeout=timeouts.UNBOUNDED)
        return True
    try:
        proc.wait(timeout=timeouts.LOCAL)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            proc.wait(timeout=timeouts.QUICK)
        except subprocess.TimeoutExpired:
            log.warn(f"pi process group {proc.pid} did not reap after SIGKILL")
            return False
    return True


def _close_pipes(proc: subprocess.Popen) -> None:
    """Close the three pipes, tolerating one the child already broke.

    Popen.__exit__ does this, but it also reaps unbounded; this is the half
    worth having on a path that cannot afford to block.
    """
    for pipe in (proc.stdout, proc.stderr, proc.stdin):
        if pipe is None:
            continue
        try:
            pipe.close()
        except (BrokenPipeError, OSError, ValueError):
            pass


@contextmanager
def _rpc_process(cmd: list[str], **spawn) -> Iterator[subprocess.Popen]:
    """Start Pi in its own process session, killing the group on the way out.

    ``start_new_session`` is what makes the child a group leader, so that
    `_wait_for_exit` can reach the tools it spawned rather than only Pi itself.
    It also takes the child out of the terminal's foreground group, which means
    a Ctrl-C no longer reaches it — the interrupt lands on this process alone
    and would otherwise leave a detached agent running against the account with
    nothing holding a handle to it. The kill on the way out is the other half
    of that flag, and `core.proc._run_in_own_group` pairs the two the same way.

    The pipes are closed and the child reaped by hand rather than by entering
    Popen. Its __exit__ reaps with an unbounded `self.wait()` for anything but
    a KeyboardInterrupt, so a group that survived SIGKILL would hang the
    unwinding of an ordinary exception — the same silent hang this module
    exists to avoid, on the one path with an exception already in flight. The
    success path does not come through here at all: `_wait_for_exit` has
    already waited, on the terms that path needs.
    """
    proc = subprocess.Popen(cmd, start_new_session=True, **spawn)
    try:
        yield proc
    except BaseException:
        _kill_group(proc)
        _wait_for_exit(proc, abandoned=True)
        _close_pipes(proc)
        raise


def _get_stats_after_agent_end(proc: subprocess.Popen) -> dict:
    """Query get_session_stats after the agent has finished.

    Safe to call only after agent_end — no event interleaving.

    Returns the response's ``data``, not the response. Pi wraps every RPC reply
    in ``{type, command, success, data}``, and the caller wants the stats. The
    unwrap is here rather than at the read site because this function's name
    promises stats: reading ``tokens`` off the envelope silently yields an empty
    dict, which reaches the ledger as a run that cost money and spent no tokens.
    """
    if not _send(proc, {"type": "get_session_stats"}):
        return {}
    resp = _read_rpc_response(proc, "get_session_stats")
    data = resp.get("data")
    return data if data is not None else resp


# ── Stream progress (Pi RPC JSONL) ───────────────────────────────────────────


def _display_event(data: dict, prev_tool: str, prefix: str) -> str:
    event = parse_pi_event(data)
    if not event or event.tool_label == prev_tool:
        return prev_tool
    with _print_lock:
        print(f"{prefix}  {ANSI_DIM}▸ {event.tool_label}{ANSI_RESET}", file=sys.stderr, flush=True)
    return event.tool_label


def _parse_event_type(raw_line: str) -> tuple[str, dict]:
    """Parse a line and return (event_type, parsed_data)."""
    try:
        data = json.loads(raw_line)
    except (json.JSONDecodeError, ValueError):
        return "", {}
    return data.get("type", ""), data


BUDGET_WARN_THRESHOLD = 0.8

# An agent that has already written its file just needs to finish. One that
# has not is about to run out with nothing to show, so name the mechanism
# rather than telling it to hurry.
_WRAP_UP = "Wrap up your current analysis and write your output."
# Names Pi's own tools, because this module only ever steers a Pi run. The
# `old_string` Edit this used to prescribe is Claude's recipe: Pi's edit takes
# `edits[].oldText` and rejects an empty one, so the steer spent the agent's
# last turns on a call that could not succeed.
_WRITE_FIRST = (
    "You have NOT written your output file yet. Do that now: use the `write` "
    "tool to put your complete output into it in one call. Refine it with "
    "`edit` afterwards only if turns remain."
)


def _steer_message(warning: str, wrote_output: bool) -> str:
    return f"{warning} {_WRAP_UP if wrote_output else _WRITE_FIRST}"


def _check_limits(
    process: subprocess.Popen,
    turn_count: int, accumulated_cost: float,
    max_turns: int | None, max_budget: float | None,
    steered: bool = False,
    wrote_output: bool = False,
) -> tuple[str | None, bool]:
    """Check turn and budget limits after a turn_end.

    Returns (stop_reason, steered) where stop_reason is None if not aborting.
    Sends steer at 80% of either limit (once), abort + follow_up when exceeded.
    """
    if max_turns is not None and turn_count >= max_turns:
        _send(process, {"type": "abort"})
        _send(process, {"type": "follow_up", "message": "You were stopped due to turn limit. Summarize what you found and what remains."})
        return "max_turns", steered
    if max_budget is not None and accumulated_cost > max_budget:
        _send(process, {"type": "abort"})
        _send(process, {"type": "follow_up", "message": "You were stopped due to budget limit. Summarize what you found and what remains."})
        return "max_budget", steered

    if not steered:
        if max_budget is not None and accumulated_cost >= max_budget * BUDGET_WARN_THRESHOLD:
            warning = f"Budget warning: {accumulated_cost:.2f}/{max_budget:.2f} USD consumed."
            _send(process, {"type": "steer", "message": _steer_message(warning, wrote_output)})
            steered = True
        elif max_turns is not None and turn_count >= int(max_turns * BUDGET_WARN_THRESHOLD):
            warning = f"Turn warning: {turn_count}/{max_turns} turns used."
            _send(process, {"type": "steer", "message": _steer_message(warning, wrote_output)})
            steered = True

    return None, steered


@dataclass
class StreamResult:
    """Outcome of consuming a Pi RPC event stream.

    ``model`` is the model string Pi itself reports on ``message_end`` events
    (e.g. ``claude-haiku-4-5@20251001``), not the caller-requested alias —
    the same spelling ``pi_prompt_result`` keys ``cost_by_model`` on, so a
    session's cost lands under one key however it was invoked. It is None
    when the stream ended with no message_end (e.g. an immediate abort).

    ``error`` is the text of the RPC response that ended the run, and None for
    a run that ended any other way. It is the only evidence a rejected prompt
    leaves: Pi answers the command and then waits, so nothing downstream can
    infer the failure from the events or from the exit status.
    """
    turn_count: int
    accumulated_cost: float
    stop_reason: str
    model: str | None = None
    error: str | None = None


def _undelivered_prompt(log_file) -> StreamResult:
    """The result of a run whose prompt never reached Pi.

    Logged into the session file in Pi's own response shape, so the record that
    explains the failure is where every other refusal leaves one.
    """
    log.error(f"pi refused the run: {_PROMPT_UNDELIVERED}")
    log_file.write(json.dumps({
        "type": "response", "command": "prompt", "success": False,
        "error": _PROMPT_UNDELIVERED,
    }) + "\n")
    log_file.flush()
    return StreamResult(0, 0.0, "error", None, _PROMPT_UNDELIVERED)


def _consume_stream(
    process: subprocess.Popen, log_file, prefix: str,
    max_turns: int | None = None,
    max_budget: float | None = None,
) -> StreamResult:
    """Consume the RPC event stream, enforcing turn and budget limits.

    stop_reason is one of: "completed", "max_turns", "max_budget", "error".
    """
    prev_tool = ""
    turn_count = 0
    accumulated_cost = 0.0
    stop_reason = "completed"
    steered = False
    aborted = False
    wrote_output = False
    model = None
    error = None

    for raw_line in process.stdout:
        log_file.write(raw_line)
        log_file.flush()

        event_type, data = _parse_event_type(raw_line)

        # Checked after the raw line is logged, so the one message explaining
        # the failure is in the session log, and before every parser below,
        # which the unconditional skip this replaces also kept response events
        # away from.
        response_error = _rpc_response_error(data) if event_type == "response" else None
        if response_error:
            log.error(f"{prefix}pi refused the run: {response_error}")
            stop_reason, error = "error", response_error
            break
        if event_type == "response":
            continue

        prev_tool = _display_event(data, prev_tool, prefix)
        wrote_output = wrote_output or pi_write_tool_used(data)

        msg_cost = parse_pi_cost(data)
        if msg_cost is not None:
            accumulated_cost += msg_cost
            model = data.get("message", {}).get("model") or model

        if event_type == "turn_end":
            turn_count += 1
            stop, steered = _check_limits(process, turn_count, accumulated_cost, max_turns, max_budget, steered, wrote_output) if not aborted else (None, steered)
            stop_reason, aborted = (stop, True) if stop else (stop_reason, aborted)

        if event_type == "agent_end":
            break

    return StreamResult(turn_count, accumulated_cost, stop_reason, model, error)


# ── Result record generation ─────────────────────────────────────────────────


def _write_result_record(
    session_log: str,
    stop_reason: str,
    turn_count: int,
    cost: float,
    duration_ms: int,
    stats: dict,
    model: str | None = None,
    *,
    error: str | None = None,
):
    """Write a Claude-compatible result record to the session log.

    Maps Pi's get_session_stats fields to Claude's result record format
    so _parse_session_cost() and parse_session_usage() work without changes.

    ``stats`` is the ``data`` of a get_session_stats response, which is what
    _get_stats_after_agent_end returns — not the response envelope.

    ``error`` is the RPC error that ended the run, and is what makes the record
    an error record: `agent.session._diagnose_result_type` reads ``is_error``
    first and the text second, so without both an auth failure reads as a clean
    run that happened to write nothing.
    """
    tokens = stats.get("tokens", {})
    total_cost = stats.get("cost", cost)

    record = {
        "type": "result",
        "subtype": "success" if stop_reason == "completed" else stop_reason,
        # Derived from the error text rather than from stop_reason: max_turns
        # and max_budget are classified from the subtype and must keep a false
        # flag, or every turn-exhausted run reads as a crash.
        "is_error": error is not None,
        "total_cost_usd": total_cost,
        "num_turns": turn_count,
        "duration_ms": duration_ms,
        "usage": {
            "input_tokens": tokens.get("input", 0),
            "output_tokens": tokens.get("output", 0),
            "cache_read_input_tokens": tokens.get("cacheRead", 0),
            "cache_creation_input_tokens": tokens.get("cacheWrite", 0),
        },
    }

    # Claude's result record carries per-model costs and the ledger reads them
    # into cost_by_model; without this every Pi row is blank under
    # `otto-log stats --by model`. Pi reports one model per session here, so the
    # whole cost belongs to it.
    if model:
        record["modelUsage"] = {
            model: {
                "costUSD": total_cost,
                "inputTokens": tokens.get("input", 0),
                "outputTokens": tokens.get("output", 0),
                "cacheReadInputTokens": tokens.get("cacheRead", 0),
                "cacheCreationInputTokens": tokens.get("cacheWrite", 0),
            }
        }
    if error is not None:
        record["error"] = error
    with open(session_log, "a") as f:
        f.write(json.dumps(record) + "\n")


def _prompt_with_dirs(inv: AgentInvocation) -> str:
    """The prompt text, with the readable directories named in it.

    Pi has no --add-dir flag, so the directories an agent may read outside its
    cwd reach it as prose or not at all.
    """
    if not inv.add_dirs:
        return inv.prompt
    dir_lines = "\n".join(f"  - {d}" for d in inv.add_dirs)
    return f"\nAccessible directories:\n{dir_lines}\n\n{inv.prompt}"


def _exit_code(proc: subprocess.Popen, stream: StreamResult) -> int:
    """The status a caller should see for this run.

    Pi shuts down cleanly after refusing a prompt, so its own status is 0 for a
    run that never started. Its status still wins when it reported one — that
    is the more specific answer — and the substitute only covers the zero.
    """
    if stream.error and not proc.returncode:
        return _RPC_ERROR_EXIT_CODE
    return proc.returncode


# ── Public interface ──────────────────────────────────────────────────────────


def preflight(models: Mapping[str, Sequence[str]], trail) -> bool:
    """No pre-run checks — Pi resolves models and provider routing itself.

    ``models`` and ``trail`` are accepted for interface parity with the
    Claude backend.
    """
    return True


def prompt(
    text: str, *, cwd: str, model: str | None = None,
    thinking: str | None = None, provider: str | None = None,
) -> tuple[str, int, ai_usage.SessionUsage | None]:
    """Stateless text-in/text-out via pi -p. Returns (text, exit_code, usage).

    Usage is read from the JSON stream — see `pi_prompt_result`. It was reported
    as None here for as long as the command omitted ``--mode json``, which is
    what made every prompt call invisible to the ledger; on this machine that
    would have been 953 rows and $230 of the Claude history had Pi been serving
    them.

    ``--thinking`` and ``--provider`` are global flags here, so a prompt honours
    both exactly as the agent modes do.
    """
    cmd = _build_prompt_cmd(model=model, provider=provider, thinking=thinking)
    result = subprocess.run(cmd, input=text, capture_output=True, text=True, cwd=cwd,
                            timeout=timeouts.UNBOUNDED)
    reply, usage = pi_prompt_result(result.stdout)
    return reply, result.returncode, usage


def invoke_agent(inv: AgentInvocation) -> int:
    """Full agent with RPC streaming to session log. Returns exit code.

    Uses Pi's RPC mode for bidirectional control:
    - add_dirs are injected into the prompt text (Pi has no --add-dir flag)
    - max_turns is enforced by counting turn_end events and sending abort
    - max_budget is enforced by accumulating message_end costs and sending abort
    - A Claude-compatible result record is written at the end for cost tracking
    """
    ext = str(REVIEW_EXTENSION) if REVIEW_EXTENSION.is_file() else None
    cmd = _build_agent_cmd(inv, extension=ext)
    spawn = dict(
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=inv.cwd,
        env=_guard_env(inv) if ext else agent_env(inv),
    )

    with _rpc_process(cmd, **spawn) as proc:
        return _drive_agent(inv, proc)


def _drive_agent(inv: AgentInvocation, proc: subprocess.Popen) -> int:
    """Prompt Pi, consume its stream, and record what the run cost."""
    full_prompt = _prompt_with_dirs(inv)
    prefix = f"  {ANSI_DIM}[{inv.label}]{ANSI_RESET} " if inv.label else ""
    start_time = time.monotonic()

    # Send the prompt via RPC. A prompt that cannot be written means Pi is
    # already gone, so there is no stream to consume and no stats to ask for.
    with open(inv.session_log, "w") as log_fh:
        if _send(proc, {"type": "prompt", "message": full_prompt}):
            stream = _consume_stream(
                proc, log_fh, prefix,
                max_turns=inv.max_turns, max_budget=inv.max_budget,
            )
        else:
            stream = _undelivered_prompt(log_fh)

    duration_ms = int((time.monotonic() - start_time) * 1000)

    # Query authoritative stats after agent is done. A run Pi refused spent
    # nothing, so the query would buy a round trip for four zeroes — and it is
    # the one thing on this path that writes to a child that may already be
    # gone, then reads until a reply that will never come.
    stats = {} if stream.error else _get_stats_after_agent_end(proc)

    # Write Claude-compatible result record
    _write_result_record(
        inv.session_log, stream.stop_reason, stream.turn_count,
        stream.accumulated_cost, duration_ms, stats, stream.model or inv.model,
        error=stream.error,
    )

    return _finish(proc, stream, inv.session_log)


def _finish(proc: subprocess.Popen, stream: StreamResult, session_log: str) -> int:
    """Shut the RPC process down and report the exit code the caller should see."""
    # Close stdin to terminate the RPC process — tolerate early exit
    try:
        proc.stdin.close()
    except BrokenPipeError:
        pass
    # Reading stderr blocks until the child closes it, so it is only safe once
    # the child is gone. A group that survived SIGKILL never closes it.
    if _wait_for_exit(proc, abandoned=stream.error is not None):
        _log_stderr_on_failure(proc, session_log)
    return _exit_code(proc, stream)


def invoke_fix(inv: AgentInvocation) -> int:
    """Agent with workspace write access via RPC. Returns exit code.

    Uses RPC mode for budget tracking and turn limits, same as invoke_agent.
    If session_log is empty, events are consumed but not persisted.
    """
    ext = str(REVIEW_EXTENSION) if REVIEW_EXTENSION.is_file() else None
    cmd = _build_fix_cmd(inv, extension=ext)
    spawn = dict(
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=inv.cwd,
        env=_guard_env(inv) if ext else agent_env(inv),
    )

    with _rpc_process(cmd, **spawn) as proc:
        return _drive_fix(inv, proc)


def _drive_fix(inv: AgentInvocation, proc: subprocess.Popen) -> int:
    """Prompt Pi and consume its stream, persisting the run only if asked to."""
    start_time = time.monotonic()

    log_path = inv.session_log if inv.session_log else os.devnull
    with open(log_path, "w") as log_file:
        if _send(proc, {"type": "prompt", "message": _prompt_with_dirs(inv)}):
            stream = _consume_stream(
                proc, log_file, "",
                max_turns=inv.max_turns, max_budget=inv.max_budget,
            )
        else:
            stream = _undelivered_prompt(log_file)

    duration_ms = int((time.monotonic() - start_time) * 1000)

    if inv.session_log:
        stats = {} if stream.error else _get_stats_after_agent_end(proc)
        _write_result_record(
            inv.session_log, stream.stop_reason, stream.turn_count,
            stream.accumulated_cost, duration_ms, stats, stream.model or inv.model,
            error=stream.error,
        )

    return _finish(proc, stream, inv.session_log)
