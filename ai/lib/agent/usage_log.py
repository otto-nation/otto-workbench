"""The shell bridge into the usage ledger.

It renders a stream-json feed while teeing the raw stream, unwraps a
`--output-format json` envelope, and appends one ledger record from a teed
file. Run by `ai-usage-log` for the two shell callers that cannot use
`agent.backend` (`run-auto-task`).

Not: parsing usage (`agent.usage`), rendering an event
(`agent.backend_events`), argument parsing (`cli.ai_usage_log`).
"""

# doc-group: backend

from __future__ import annotations

import json
import sys
from pathlib import Path

import agent.backend_events
import agent.usage


def _tee(path: str | None):
    return open(path, "w", encoding="utf-8") if path else None


def _render_line(raw_line: str, sink) -> None:
    """Tee one line and print its readable form, if it has one."""
    if sink:
        sink.write(raw_line)
        sink.flush()
    stripped = raw_line.strip()
    if not stripped:
        return
    if not stripped.startswith("{"):
        print(stripped, flush=True)
        return
    display = agent.backend_events.claude_display_text(raw_line)
    if display:
        print(display, flush=True)


def render(tee: str | None) -> int:
    """Echo a stream-json feed as human-readable text, keeping the raw stream.

    Lines that are not Claude events pass through verbatim: stderr is merged into
    this stream by the caller, and losing it would make failures undiagnosable.
    """
    sink = _tee(tee)
    try:
        for raw_line in sys.stdin:
            _render_line(raw_line, sink)
    finally:
        if sink:
            sink.close()
    return 0


def unwrap(tee: str | None) -> int:
    """Print the reply text from a JSON envelope, passing plain output through.

    A reply with no JSON envelope passes through intact.
    """
    stdout = sys.stdin.read()
    sink = _tee(tee)
    if sink:
        sink.write(stdout)
        sink.close()
    try:
        envelope = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        envelope = None
    reply = envelope.get("result") if isinstance(envelope, dict) else None
    sys.stdout.write(reply if isinstance(reply, str) else stdout)
    return 0


def _usage_from(path: str) -> agent.usage.SessionUsage | None:
    """Read usage from a teed file, whether it holds JSONL or one JSON envelope."""
    if not path or not Path(path).is_file():
        return None
    usage = agent.usage.parse_session_log(path)
    if usage != agent.usage.SessionUsage():
        return usage
    try:
        envelope = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(envelope, dict):
        return None
    envelope_usage = agent.usage.usage_from_records([envelope])
    return envelope_usage if envelope_usage != agent.usage.SessionUsage() else None


def record_from_log(
    from_log: str,
    *,
    script: str,
    entry_point: str,
    backend: str = "claude",
    model: str | None = None,
    exit_code: int = 0,
    task: str | None = None,
    repo: str | None = None,
    pr: str | None = None,
) -> int:
    """Append one ledger record, or none when the call produced nothing to measure."""
    usage = _usage_from(from_log)
    if usage is None:
        return 0
    agent.usage.record(
        script=script, entry_point=entry_point, backend=backend,
        model=model, usage=usage, exit_code=exit_code,
        task=task, repo=repo, pr=pr,
    )
    return 0
