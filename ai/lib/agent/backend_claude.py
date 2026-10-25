"""Claude Code CLI backend for agent.backend.

Implements preflight(), prompt(), invoke_agent(), and invoke_fix() by
building `claude -p` commands and running them as subprocesses.

A failure is logged from whichever stream carried the detail, and from the exit
code alone when neither did. `claude -p` reports some failures on stdout with an
empty stderr — a usage limit is the common one — so a stderr-only error message
prints nothing at all and leaves the caller reporting a bare exit code with no
reason attached.
"""

# doc-group: backend

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import agent.machine_env
import agent.usage
import core.children
import core.log
import core.timeouts
import agent.vertex_quota
from agent.backend import AgentInvocation, agent_env
from agent.backend_events import _log_stderr, claude_display_text, parse_claude_event
from agent.context_ceiling import claude_window
from core.log import ANSI_DIM, ANSI_RESET, _print_lock
from core.phases import Thinking


# ── Agent resolution ─────────────────────────────────────────────────────────

_AGENTS_DIR = Path.home() / ".claude" / "agents"


def _load_agent_def(name: str) -> dict | None:
    """Load a custom agent definition from ~/.claude/agents/{name}.md.

    --bare mode skips auto-discovery of custom agents, so we read the file
    and pass it via --agents JSON to make it available to the CLI.
    """
    path = _AGENTS_DIR / f"{name}.md"
    if not path.exists():
        return None

    content = path.read_text()
    if not content.startswith("---"):
        return {"description": name, "prompt": content}

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {"description": name, "prompt": content}

    description = name
    for line in parts[1].strip().splitlines():
        if line.startswith("description:"):
            description = line.split(":", 1)[1].strip()
            break

    return {"description": description, "prompt": parts[2].strip()}


# ── Stream progress ───────────────────────────────────────────────────────────


def _display_event(raw_line: str, prev_tool: str, prefix: str) -> str:
    event = parse_claude_event(raw_line)
    if not event or event.tool_label == prev_tool:
        return prev_tool
    with _print_lock:
        print(f"{prefix}  {ANSI_DIM}▸ {event.tool_label}{ANSI_RESET}", file=sys.stderr, flush=True)
    return event.tool_label


def stream_progress(process: subprocess.Popen, session_log: str, label: str = ""):
    prev_tool = ""
    prefix = f"  {ANSI_DIM}[{label}]{ANSI_RESET} " if label else ""
    with open(session_log, "w") as log:
        for raw_line in process.stdout:
            log.write(raw_line)
            log.flush()
            prev_tool = _display_event(raw_line, prev_tool, prefix)


# ── Effort ───────────────────────────────────────────────────────────────────

# Thinking's levels below anything `claude --effort` takes; each runs at `low`.
_BELOW_EFFORT = frozenset({Thinking.OFF, Thinking.MINIMAL})
# The levels `claude --effort` takes: the rest of Thinking's vocabulary. A model
# lacking one falls back to the highest it has at or below it (Claude Code's
# model-config docs: `xhigh` runs as `high` on Opus 4.6).
EFFORT_LEVELS = frozenset(Thinking) - _BELOW_EFFORT
# What turns thinking off where a model allows it. Claude Code documents it as a
# no-op on the models that cannot think less than `low` — Opus, Sonnet and Haiku
# 5.5 and the Fable models — so `off` there runs at `low` and no lower.
_THINKING_OFF_ENV = ("MAX_THINKING_TOKENS", "0")


def _normalized(thinking: str | None) -> str:
    # Operator input reaches here as written (agent.phases.resolve_thinking).
    return (thinking or "").strip().lower()


def effort(thinking: str | None) -> str | None:
    """The ``--effort`` level a thinking level runs at, or None to pass no flag.

    ``off`` and ``minimal`` have no effort spelling and run at ``low``, the least
    the CLI can be asked for — not at its default, which would be more thinking,
    not less; ``off`` also sets ``MAX_THINKING_TOKENS=0`` (`thinking_env`). An
    unknown level is None: the CLI ignores one with a warning and runs at its
    default, so passing it would add the warning and change nothing.
    """
    level = _normalized(thinking)
    if level in EFFORT_LEVELS:
        return level
    if level in _BELOW_EFFORT:
        return str(Thinking.LOW)
    return None


def thinking_env(thinking: str | None) -> dict[str, str]:
    """The environment a thinking level adds: thinking off for ``off``, else nothing."""
    if _normalized(thinking) == Thinking.OFF:
        return dict([_THINKING_OFF_ENV])
    return {}


def _effort_args(thinking: str | None) -> list[str]:
    level = effort(thinking)
    return ["--effort", level] if level else []


# ── Compaction ───────────────────────────────────────────────────────────────


def _autocompact_args(compact_at: int | None) -> list[str]:
    """``--autocompact`` at the ceiling *compact_at*, or nothing when there is none.

    A flag rather than the ``autoCompactWindow`` sync writes, because ``--bare``
    reads no user settings: without it a pipeline agent compacts at the model's
    full window whatever the operator chose. The prompt mode takes none — it is
    one turn, so nothing grows to compact.
    """
    window = claude_window(compact_at).window
    return ["--autocompact", str(window)] if window else []


# ── Command builders ──────────────────────────────────────────────────────────


def _base_cmd() -> list[str]:
    """Shared flags used by both agent and fix commands.

    ``--bare`` skips hooks, plugins and — the part that matters here — CLAUDE.md
    auto-discovery. What puts the operator's memory back is ``--add-dir``, which
    every command built from this one passes: the flag restores memory loading
    wholesale, not merely access to the directory it names. So the coding rules
    an agent runs under arrive through a flag passed for filesystem reasons.

    That coupling is undocumented Claude behaviour and load-bearing. A command
    built without ``--add-dir`` silently loses the whole rule set with no error
    and no missing-file to notice, which is why the call sites are pinned by
    test rather than left to the reader — see `TestEveryCommandCarriesAnAddDir`.
    """
    return [
        "claude", "-p", "--bare", "--verbose",
        "--permission-mode", "acceptEdits",
        "--allowedTools", "Bash(*)",
        "--disable-slash-commands",
    ]


def _build_agent_cmd(inv: AgentInvocation) -> list[str]:
    add_dir_args = []
    for d in inv.add_dirs:
        add_dir_args += ["--add-dir", d]
    cmd = [*_base_cmd(), "--output-format", "stream-json", *add_dir_args]
    if inv.agent:
        agent_def = _load_agent_def(inv.agent)
        if agent_def:
            cmd += ["--agents", json.dumps({inv.agent: agent_def})]
        cmd += ["--agent", inv.agent]
    if inv.max_turns is not None:
        cmd += ["--max-turns", str(inv.max_turns)]
    if inv.max_budget is not None:
        cmd += ["--max-budget-usd", str(inv.max_budget)]
    if inv.model:
        cmd += ["--model", inv.model]
    return cmd + _effort_args(inv.thinking) + _autocompact_args(inv.compact_at)


# A fix agent edits the worktree; it has no GitHub business. Every outward write
# in this tool waits for `--post` (see `publishing`), and an agent shelling out to
# `gh` is a way around that gate rather than a use of it — a `--fix` run without
# `--post` that edits the PR has published something nobody approved, and left no
# draft record of it either. Denied at the tool call so the fix templates' rule is
# enforced rather than trusted. A comment answered by rewriting the PR description
# is drafted to a file instead; `pr.comments.deliver_pr_body` sends it through
# the gated client.
# The git half is the same argument turned inward. The engine commits for the
# agent (`fix.engine.run` → `git.land.land`), scoped to the paths it watched the
# agent touch, so an agent that commits, rebases or resets is not finishing the
# job early — it is landing work outside the one scope the pass can account for,
# under a message nothing in this codebase wrote. `agent.templates.ROLE_BLOCK`
# has always said so in prose, and says of itself that "negation is weaker than
# omission"; this is that rule where the tool call can see it.
#
# The Pi backend enforces the same list by a different mechanism, since `--tools`
# allowlists tool names and cannot bar one bash command: see
# WRITE_STATEMENT_PATTERNS in ai/pi/extensions-cli/detect.ts, which this is
# kept in step with. A command added to one belongs in the other.
#
# ceiling: the two are not equally tight, and this half is the loose one. A
# `Bash(git commit:*)` matcher is a prefix match, so `git -C /repo commit -m x`
# and `git --no-pager commit -m x` are not the prefix and are not denied — the
# same hole the Pi pattern had until GIT_WRITE_SUBCOMMANDS started reaching past
# global flags, and one that cannot be closed here because the matcher grammar
# has no way to express "any flags, then this subcommand". What holds on this
# backend is the fix templates' prose and the engine committing for the agent.
# Upgrade if Claude Code grows a regex or argv-aware permission matcher.
FIX_DENIED_TOOLS = ",".join((
    "Bash(gh:*)",
    "Bash(git commit:*)",
    "Bash(git push:*)",
    "Bash(git rebase:*)",
    "Bash(git reset:*)",
    "Bash(git checkout:*)",
    "Bash(git switch:*)",
    "Bash(git restore:*)",
    "Bash(git stash:*)",
    "Bash(git clean:*)",
    "Bash(git merge:*)",
    "Bash(git cherry-pick:*)",
    "Bash(git revert:*)",
    "Bash(git apply:*)",
    "Bash(git am:*)",
))


def _build_fix_cmd(inv: AgentInvocation) -> list[str]:
    add_dir_args = []
    for d in inv.add_dirs:
        add_dir_args += ["--add-dir", d]
    cmd = [
        *_base_cmd(), "--disallowedTools", FIX_DENIED_TOOLS,
        "--output-format", "stream-json", *add_dir_args,
    ]
    if inv.max_turns is not None:
        cmd += ["--max-turns", str(inv.max_turns)]
    if inv.max_budget is not None:
        cmd += ["--max-budget-usd", str(inv.max_budget)]
    if inv.model:
        cmd += ["--model", inv.model]
    return cmd + _effort_args(inv.thinking) + _autocompact_args(inv.compact_at)


# What a stateless prompt is not allowed to do. Read stays: it cannot block and
# cannot write, and a prompt that reads a file it was asked about is still
# answering with text.
#
# Verified against Claude Code 2.1.265, because the guess this replaces was
# wrong. The comment here used to say that omitting `--permission-mode
# acceptEdits` left a tool call refused by the default mode. It does not: asked
# to run `echo`, `claude -p --bare` ran it, reported the output and recorded no
# entry in `permission_denials`, and asked to name its tools it answered
# "Bash\nEdit\nRead". So this path had the same hole as the Pi one — a stateless
# prompt holding a shell — and was only ever protected by the models not
# reaching for it.
#
# A denylist rather than `--allowedTools`: the flag is variadic, so the empty
# value that would express "nothing" is not a spelling this repo has verified,
# whereas naming the three tools that execute or write is exact.
PROMPT_DENIED_TOOLS = ("Bash", "Edit", "Write")


def _build_prompt_cmd(model: str | None = None, thinking: str | None = None) -> list[str]:
    # --output-format needs --print, which -p already supplies. Without it the reply
    # carries no usage and every prompt() call goes unmeasured.
    cmd = [
        "claude", "-p", "--bare", "--output-format", "json",
        "--disallowedTools", *PROMPT_DENIED_TOOLS,
    ]
    if model:
        cmd += ["--model", model]
    return cmd + _effort_args(thinking)


# ── Subprocess helpers ───────────────────────────────────────────────────────


def _send_stdin(proc: subprocess.Popen, text: str) -> None:
    """Write text to subprocess stdin and close, tolerating early exit."""
    try:
        proc.stdin.write(text)
    except BrokenPipeError:
        pass
    try:
        proc.stdin.close()
    except BrokenPipeError:
        pass


# ── Public interface ──────────────────────────────────────────────────────────


# Where the Claude CLI sends requests, keyed by the variable that points it
# there. Named with Pi's provider ids so `otto-log stats --by provider` groups
# a Claude-on-Vertex call with a Pi one served by the same deployment.
_CLI_PROVIDERS = (
    ("CLAUDE_CODE_USE_VERTEX", "google-vertex-claude"),
    ("CLAUDE_CODE_USE_BEDROCK", "amazon-bedrock"),
)
_DEFAULT_PROVIDER = "anthropic"
# Every provider the Claude CLI can be pointed at: the only ones whose models a
# Claude Code session, or this backend, can run.
SERVED_PROVIDERS = frozenset(provider for _, provider in _CLI_PROVIDERS) | {_DEFAULT_PROVIDER}


def ledger_provider() -> str:
    """The provider the Claude CLI serves requests from, for the usage ledger.

    The CLI's stream does not name one, so it is read from the variables the
    CLI itself reads; none set is the first-party API.
    """
    for var, provider in _CLI_PROVIDERS:
        if os.environ.get(var) == "1":
            return provider
    return _DEFAULT_PROVIDER


def ledger_thinking(thinking: str | None) -> str | None:
    """The level a call asked for, for the usage ledger: the ``--effort`` it was given.

    The CLI's stream names no level, so this is the request, not what served
    it — a model lacking the level runs at a lower one, and nothing reports
    which. It is after the same mapping, so a phase written ``minimal`` is
    recorded as the ``low`` it asked for rather than a level it could not.
    """
    return effort(thinking)


def preflight(models: Mapping[str, Sequence[str]], trail) -> bool:
    """Check Vertex AI quota when the CLI is pointed at Vertex.

    No-ops on the first-party API, where model availability is not a
    per-project allocation the client can inspect ahead of time.
    """
    return agent.vertex_quota.run_preflight(models, trail)


_FAILURE_DETAIL_MAX_CHARS = 2000


def _failure_detail(result: subprocess.CompletedProcess) -> str:
    """Describe a nonzero `claude -p` exit for the log.

    The CLI reports some failures — usage limits among them — on stdout with an
    empty stderr, so a stderr-only message logs nothing at all and the caller is
    left reporting a bare exit code with no reason attached.
    """
    detail = result.stderr.strip() or result.stdout.strip()
    if not detail:
        return f"claude exited {result.returncode} with no output"
    return detail[:_FAILURE_DETAIL_MAX_CHARS]


def prompt(
    text: str, *, cwd: str, model: str | None = None,
    thinking: str | None = None, provider: str | None = None,
) -> tuple[str, int, agent.usage.SessionUsage | None]:
    """Stateless text-in/text-out via claude -p. Returns (text, exit_code, usage).

    usage is None when the reply carried no envelope to measure.

    ``thinking`` becomes ``--effort`` (see `effort`), as on this backend's agent
    modes. ``provider`` is accepted and dropped: the CLI is pointed at a provider
    by its environment, not by a flag.
    """
    # Raises MachineEnvUnavailable; backend.prompt turns it into exit 1.
    env = {**agent.machine_env.base_env(cwd), **thinking_env(thinking)}
    cmd = _build_prompt_cmd(model=model, thinking=thinking)
    result = subprocess.run(cmd, input=text, capture_output=True, text=True, cwd=cwd,
                            timeout=core.timeouts.UNBOUNDED, env=env)
    if result.returncode != 0:
        core.log.dim(_failure_detail(result))
    reply, usage = _unwrap_prompt_output(result.stdout)
    return reply, result.returncode, usage


def _unwrap_prompt_output(stdout: str) -> tuple[str, agent.usage.SessionUsage | None]:
    """Pull the reply text and usage out of a --output-format json envelope.

    Falls back to the raw stdout if it is not the expected envelope, so a CLI
    output change degrades to the previous behavior instead of breaking callers.
    An unparseable reply yields no usage rather than a zeroed one — nothing was
    measured, and a zero row would read as a free call.
    """
    try:
        envelope = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return stdout, None
    if not isinstance(envelope, dict):
        return stdout, None
    usage = agent.usage.usage_from_records([envelope])
    reply = envelope.get("result")
    return (reply if isinstance(reply, str) else stdout), usage


def _spawn_env(inv: AgentInvocation) -> dict[str, str]:
    """The subprocess environment, with this run's operator rule prefix applied.

    A thinking level of ``off`` adds the variable that turns thinking off (see
    `thinking_env`); ``--effort`` alone cannot ask for less than ``low``.

    ``rules_home`` is backend-neutral; this backend maps it to
    ``CLAUDE_CONFIG_DIR``. Empty means inherit whatever the operator already
    has — no key is written. The path must be absolute: the CLI rejects a
    relative ``CLAUDE_CONFIG_DIR`` outright.

    Not applied inside ``agent_env``: that helper is backend-blind and shared.
    A Claude key there would be the same layering mistake one level up.
    """
    env = {**agent_env(inv), **thinking_env(inv.thinking)}
    if not inv.rules_home:
        return env
    home = Path(inv.rules_home).expanduser()
    if not home.is_absolute():
        raise ValueError(
            f"AgentInvocation.rules_home must be absolute; got {inv.rules_home!r}"
        )
    env["CLAUDE_CONFIG_DIR"] = str(home)
    return env


def invoke_agent(inv: AgentInvocation) -> int:
    """Full agent with JSONL streaming to session log. Returns exit code."""
    cmd = _build_agent_cmd(inv)
    with core.children.owned(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=inv.cwd,
        env=_spawn_env(inv),
    ) as proc:
        _send_stdin(proc, inv.prompt)
        stream_progress(proc, inv.session_log, label=inv.label)
        # stream_progress has already read stdout to EOF, so this is a reap of
        # a process that has finished writing — not a hang detector. Claude's
        # own turn ends the CLI; a bound here would only fire if it closed the
        # stream and then refused to exit, which is not a failure this
        # pipeline sees.
        proc.wait(timeout=core.timeouts.UNBOUNDED)
    _log_stderr(proc, inv.session_log)
    return proc.returncode


def invoke_fix(inv: AgentInvocation) -> int:
    """Agent with workspace write access, progress echoed to stderr. Returns exit code."""
    cmd = _build_fix_cmd(inv)
    with core.children.owned(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        cwd=inv.cwd,
        env=_spawn_env(inv),
    ) as proc:
        _send_stdin(proc, inv.prompt)
        _stream_fix_output(proc, inv.session_log)
        # Same reap as invoke_agent: stdout is already drained. The fix CLI
        # ends when the turn does; nothing this wait could time out on is a
        # hang.
        proc.wait(timeout=core.timeouts.UNBOUNDED)
    return proc.returncode


def _stream_fix_line(raw_line: str, sink) -> None:
    """Capture one raw line and echo its readable form, if it has one."""
    if sink:
        sink.write(raw_line)
        sink.flush()
    display = claude_display_text(raw_line)
    if display:
        print(display, file=sys.stderr, flush=True)


def _stream_fix_output(proc: subprocess.Popen, session_log: str) -> None:
    """Echo readable progress to stderr while capturing the raw stream for accounting.

    stdout is stream-json now, so the raw lines it used to print would be unreadable.
    """
    sink = open(session_log, "w") if session_log else None
    try:
        for raw_line in proc.stdout:
            _stream_fix_line(raw_line, sink)
    finally:
        if sink:
            sink.close()
