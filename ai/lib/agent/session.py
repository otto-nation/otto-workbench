"""What an agent run left behind: its cost, its diagnosis, its salvage.

Everything here reads a session log after the fact. Running the agent is
``agent.invoke``'s job and resolving which model it ran with is
``agent.phases``'s; this module is what the pipeline asks once the log exists —
what the run cost, why it produced nothing, whether the document it was denied
permission to save can still be recovered.

The split matters for the quota retry, whose two halves live apart: this module
reads the 429 out of the log, and ``agent.invoke`` decides how long to wait.
"""

# doc-group: pipeline

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from core import log
from agent.diagnosis import Diagnosis, DiagnosisKind
from agent.backend_events import PI_RPC_EVENT_TYPES, is_write_tool, pi_wrote_output

CONSECUTIVE_FAIL_THRESHOLD = 3

_TRANSIENT_ERROR_MARKERS = (
    "FailedToOpenSocket",
    "ConnectionRefused",
    "ConnectionReset",
    "Connection to the API was lost",
    "ECONNREFUSED",
    "ECONNRESET",
    "ETIMEDOUT",
    "socket hang up",
)


# ── Cost tracking ────────────────────────────────────────────────────────────

def _try_parse_json(line: str) -> dict | None:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def read_jsonl(log_path: str | Path) -> list[dict]:
    """Every parseable record in a JSONL file, in order.

    A line that does not parse is skipped and the rest are kept, so a log still
    being written to — or an artifact truncated by the job that died producing
    it — yields the records it did finish rather than nothing at all.

    Public because it is the machine's one JSONL reader: an agent session log is
    what it was written for, but a Go test artifact is the same format and had
    grown a second copy of this in `ci-check`. Callers that need more than one
    record type should read once and filter rather than making a pass per type.
    """
    with open(log_path) as f:
        parsed = (_try_parse_json(line) for line in f)
        return [d for d in parsed if d is not None]


def _of_type(records: list[dict], record_type: str) -> list[dict]:
    return [d for d in records if d.get("type") == record_type]


def _parse_jsonl_records(log_path: str, record_type: str) -> list[dict]:
    return _of_type(read_jsonl(log_path), record_type)


def _parse_session_cost(log_path: str) -> float:
    if not log_path or not Path(log_path).is_file():
        return 0.0
    results = _parse_jsonl_records(log_path, "result")
    return sum(r.get("total_cost_usd", 0.0) for r in results)


def _diagnose_result_type(result: dict) -> Diagnosis:
    subtype = result.get("subtype", "")
    if "max_turns" in subtype:
        return Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=result.get("num_turns"))
    if result.get("is_error"):
        errors = result.get("errors", [])
        detail = errors[0] if errors else result.get("result", result.get("error", "unknown"))
        detail = str(detail)
        kind = (
            DiagnosisKind.TRANSIENT
            if _detail_is_transient(detail)
            else DiagnosisKind.AGENT_ERROR
        )
        return Diagnosis(kind, detail=detail)
    return Diagnosis(DiagnosisKind.COMPLETED, detail=subtype)


def _tool_names_used(records: list[dict]) -> set[str]:
    """Names of every tool the agent invoked, per the session log.

    Only the Claude backend writes `assistant` records with `tool_use` blocks;
    for other backends this is empty and callers must not read that as "no
    tools were used". Guard with `_tool_use_is_observable` before drawing that
    conclusion.
    """
    return {
        block.get("name", "")
        for record in _of_type(records, "assistant")
        for block in record.get("message", {}).get("content", [])
        if block.get("type") == "tool_use"
    }


def _tool_use_is_observable(records: list[dict]) -> bool:
    """Whether an empty `_tool_names_used` means "no tools" or "cannot tell".

    Derived from the log rather than the active backend, because a log can
    outlive the run that wrote it. Any `assistant` record means the log is in
    the Claude backend's shape, where every tool call is recorded — so an empty
    tool set is a real absence.
    """
    return bool(_of_type(records, "assistant"))


def _is_pi_log(records: list[dict]) -> bool:
    """Whether these records are a Pi RPC stream rather than a Claude log.

    Keyed on the RPC event envelope Pi writes and Claude has no equivalent of.
    A log carrying neither shape is neither backend's and is left to the
    Claude-shaped path, which reports "cannot tell" rather than guessing.
    """
    return any(record.get("type") in PI_RPC_EVENT_TYPES for record in records)


def _pi_wrote_output(records: list[dict], output_path: str = "") -> bool:
    """Whether a Pi RPC log shows a write to the declared deliverable.

    The Pi half of the no-write diagnosis. Without it a Pi agent that ran to
    its own conclusion having written nothing was indistinguishable from one
    that worked, so the only thing that ever triggered a retry was exhausting
    the turn cap — and a run that circled and gave up early was written off as
    a completed review with an empty file.

    A scratch write under /tmp is not the deliverable. Counting any write here
    cleared `no_write_tool`, diagnosed as bare COMPLETED, and skipped the retry
    the live stream would still have steered. An empty `output_path` keeps the
    any-write reading, the same contract `pi_wrote_output` already documents.
    """
    return any(pi_wrote_output(record, output_path) for record in records)


def diagnose_missing_output(log_path: str, output_path: str = "") -> Diagnosis:
    """Why an agent run left no output, read from its session log.

    Public because `agent.retry` decides retryability from the returned kind.

    `output_path` is the declared deliverable. Empty means the caller has no
    single file, so any write counts — the same contract `pi_wrote_output`
    already has. Only the Pi branch reads it: the Claude branch has tool names
    to go on, not paths.

    A caller with no log to name is the same answer as a log that is not there.
    `is_file` rather than `exists`: an empty path becomes `Path(".")`, which
    exists as a directory and would pass an existence check, leaving the read
    below to fail on a directory instead of reporting a missing log.
    """
    if not log_path or not Path(log_path).is_file():
        return Diagnosis(DiagnosisKind.NO_SESSION_LOG)
    records = read_jsonl(log_path)
    gone = _deliverable_is_gone(output_path)
    results = _of_type(records, "result")
    if not results:
        if _has_quota_retry(records):
            return Diagnosis(DiagnosisKind.QUOTA_EXHAUSTED)
        return Diagnosis(DiagnosisKind.NO_RESULT_RECORD)
    diagnosis = _diagnose_result_type(results[-1])
    # An agent that ran to its own conclusion without ever calling a write tool
    # was thrashing, not working — say so instead of reporting a bare turn
    # count. An agent that called no tool at all (a one-turn refusal, say) is
    # the clearest case of this, so it counts too. A crash is excluded: the
    # error already explains the missing output, and a retry would most likely
    # reproduce it.
    crashed = diagnosis.kind in (DiagnosisKind.AGENT_ERROR, DiagnosisKind.TRANSIENT)
    if crashed:
        return diagnosis
    # Only now: a crash already explains the missing output, and saying it
    # twice pushes the cause out of the reader's way with a restatement of it.
    diagnosis = replace(diagnosis, deliverable_gone=gone)
    if _is_pi_log(records):
        if _pi_wrote_output(records, output_path):
            return diagnosis
        return replace(diagnosis, no_write_tool=True)
    if not _tool_use_is_observable(records):
        return diagnosis
    tools_used = _tool_names_used(records)
    # empty tools_used also satisfies this when observability is confirmed
    wrote = any(is_write_tool(name) for name in tools_used)
    if wrote:
        return diagnosis
    return replace(diagnosis, no_write_tool=True)


def _deliverable_is_gone(output_path: str) -> bool:
    """Whether the declared deliverable is absent rather than merely empty.

    `review.phases._touch` pre-creates the file before every phase, so an empty
    one is the ordinary shape of a run that wrote nothing and the existing
    message already names that. A file that is not there at all did not come
    from the agent declining to write: something removed it, or the phase was
    handed a path nothing created. That is the state no other field reports,
    and reporting it as "output missing" tells the reader the one thing they
    could already see. Reporting only — retryability does not read it.
    """
    return bool(output_path) and not Path(output_path).exists()


def _detail_is_transient(detail: str) -> bool:
    """Whether a backend error's text names a fault a second attempt could clear.

    Only reached from `_diagnose_result_type`, which has already established
    that the run crashed — a marker appearing in the output of a run that ended
    on its own terms is not an error report.
    """
    return any(marker in detail for marker in _TRANSIENT_ERROR_MARKERS)


def _is_model_error(log_path: str) -> bool:
    if not log_path or not Path(log_path).is_file():
        return False
    results = _parse_jsonl_records(log_path, "result")
    if not results:
        return False
    result = results[-1]
    if result.get("api_error_status") == 404:
        return True
    text = result.get("result", "")
    return isinstance(text, str) and "not available" in text.lower()


def _extract_heredoc(cmd: str) -> str:
    lines = cmd.split("\n")
    start = next((i for i, l in enumerate(lines) if "<<" in l), -1)
    if start < 0:
        return ""
    end = next((i for i in range(len(lines) - 1, start, -1) if lines[i].strip() in ("REVIEW_EOF", "EOF")), -1)
    if end < 0:
        return ""
    return "\n".join(lines[start + 1:end])


def _extract_denied_content(denial: dict) -> str:
    tool_input = denial.get("tool_input", {})
    content = tool_input.get("content", "")
    if content:
        return content
    cmd = tool_input.get("command", "")
    if "REVIEW_EOF" not in cmd and "EOF" not in cmd:
        return ""
    return _extract_heredoc(cmd)


def _collect_denied_contents(records: list[dict]) -> list[str]:
    results = _of_type(records, "result")
    denials = [d for r in results for d in r.get("permission_denials", [])]
    return [_extract_denied_content(d) for d in denials]


def _pi_write_attempts(records: list[dict], output_path: str) -> list[str]:
    """Contents a Pi agent tried to write to the deliverable, in log order.

    Pi's `tool_execution_start` carries the whole document alongside the path,
    so a write a guard refused — or one that landed somewhere else — leaves the
    findings in the log regardless of what reached the declared path. Claude's
    equivalent is `permission_denials`, which `_collect_denied_contents` reads;
    Pi writes no such record, which is why this is a second reader rather than
    a branch inside that one.

    A relative write counts. An agent refused the absolute path retries with a
    bare `review.md`, which lands in its worktree and is the exact file three
    runs lost their findings to — so the basename is matched as well as the
    resolved path. Matching is on the whole final component, never a substring,
    so a `test123.txt` beside it is not mistaken for the deliverable.
    """
    if not output_path:
        return []
    target = Path(output_path)
    resolved = target.resolve()
    contents = []
    for record in records:
        if record.get("type") != "tool_execution_start":
            continue
        args = record.get("args") or {}
        written = args.get("path")
        content = args.get("content")
        if not written or not content:
            continue
        candidate = Path(written)
        if candidate.name != target.name and candidate.resolve() != resolved:
            continue
        contents.append(content)
    return contents


def try_recover_output(log_path: str, output_path: str) -> bool:
    """Salvage a document the agent wrote but that never reached `output_path`.

    Two sources, because the two backends record a lost write differently: a
    Claude denial carries the content in its `permission_denials` record, and a
    Pi write carries it in the `tool_execution_start` that announced it.

    Public because `agent.retry` runs this before writing a run off as
    unproductive — the content is in the log either way.

    The last qualifying candidate wins. An agent refused its first write tries
    again, and the document grows across those attempts rather than shrinking;
    taking the first would recover a draft and discard the review.
    """
    if not log_path or not Path(log_path).is_file():
        return False
    records = read_jsonl(log_path)
    candidates = _collect_denied_contents(records) + _pi_write_attempts(
        records, output_path,
    )
    for content in reversed(candidates):
        if "## " not in content:
            continue
        Path(output_path).write_text(content + "\n")
        log.warn(f"Recovered review from the session log — saved to {output_path}")
        return True
    return False


# ── Quota detection ────────────────────────────────────────────────────────

def _has_quota_retry(records: list[dict]) -> bool:
    return any(
        r.get("subtype") == "api_retry" and r.get("error_status") == 429
        for r in _of_type(records, "system")
    )


def is_quota_error(log_path: str) -> bool:
    """Whether this session log shows the API turning the agent away on quota.

    Public because ``agent.invoke`` decides the backoff and this module owns
    reading a session log — the two halves of the same retry.
    """
    if not log_path or not Path(log_path).is_file():
        return False
    return _has_quota_retry(read_jsonl(log_path))


# ── Agent invocation ──────────────────────────────────────────────────────────


def build_add_dirs(wt_path: str, artifact_dir: str) -> list[str]:
    """Directories the agent may read outside its cwd.

    Never empty, and that matters for more than file access: under ``--bare``
    it is ``--add-dir`` that restores CLAUDE.md discovery, so an invocation
    built with no directory loses the operator's whole rule set silently. See
    `agent.backend_claude._base_cmd`.
    """
    return [artifact_dir, wt_path]
