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
import re
from dataclasses import replace
from pathlib import Path

from core import log
from agent.diagnosis import Diagnosis, DiagnosisKind
from agent.backend_events import (
    PI_RPC_EVENT_TYPES, is_write_tool, pi_run_error, pi_wrote_output,
)

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


def _diagnose_error_detail(detail: str) -> Diagnosis:
    """One backend error's text as a diagnosis, transient or not."""
    kind = (
        DiagnosisKind.TRANSIENT
        if _detail_is_transient(detail)
        else DiagnosisKind.AGENT_ERROR
    )
    return Diagnosis(kind, detail=detail)


def _diagnose_result_type(result: dict, records: list[dict] | None = None) -> Diagnosis:
    """Why this run ended, from its `result` record and the stream around it.

    `records` is the whole log, and it is read because the `result` record is
    not always the one holding the failure. Pi reports a failed API call on
    `agent_end` while `result` still says `subtype=success, is_error=False` —
    so a reader of `result` alone calls a transport fault a completed run,
    blames the agent for not writing, and skips the retry that would have
    cleared it. Omitted, the reader is the old `result`-only one.

    Checked only where `result` itself claims success. A `result` that reports
    its own error is the better-attributed of the two, and `max_turns` is a
    verdict about the run rather than a fault in it — an aborted last turn is
    how the cap *looks* from the envelope, so reading the envelope first there
    would relabel every truncated run as a crash.
    """
    subtype = result.get("subtype", "")
    if "max_turns" in subtype:
        return Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=result.get("num_turns"))
    # Beside max_turns and above the error check for the same reason it is: a
    # stall is a verdict the harness reached about the run, and the aborted
    # last turn it leaves behind would otherwise read as a crash in it.
    if "stalled" in subtype:
        return Diagnosis(DiagnosisKind.STALLED, num_turns=result.get("num_turns"))
    if result.get("is_error"):
        errors = result.get("errors", [])
        detail = errors[0] if errors else result.get("result", result.get("error", "unknown"))
        return _diagnose_error_detail(str(detail))
    stream_error = pi_run_error(records or [])
    if stream_error:
        return _diagnose_error_detail(stream_error)
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


def _called_any_tool(records: list[dict]) -> bool:
    """Whether the run invoked a tool at all, in either backend's log shape.

    Pi announces each call with `tool_execution_start`; Claude records a
    `tool_use` block inside an assistant message. A log carrying neither is a
    run that called nothing — or a shape this does not know, which is why the
    one caller pairs this with positive evidence of narration rather than
    treating a quiet log as proof on its own.
    """
    if any(record.get("type") == "tool_execution_start" for record in records):
        return True
    return bool(_tool_names_used(records))


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
    diagnosis = _diagnose_result_type(results[-1], records)
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
    # Narration means the agent produced a call as text *and called nothing*.
    # Without the second half this fires on any run whose commentary happens
    # to contain a markdown heading: measured over the logs on this machine,
    # 3 of 75 runs that made real tool calls — one of them 58 of them — carry
    # heading-bearing prose and would be told they had narrated. The hint
    # would then be addressed to an agent that did call its tools, about a
    # mistake it did not make.
    narrated = not _called_any_tool(records) and bool(
        _narrated_write_contents(records),
    )
    if _is_pi_log(records):
        if _pi_wrote_output(records, output_path):
            return diagnosis
        return replace(diagnosis, no_write_tool=True, narrated_call=narrated)
    if not _tool_use_is_observable(records):
        return diagnosis
    tools_used = _tool_names_used(records)
    # empty tools_used also satisfies this when observability is confirmed
    wrote = any(is_write_tool(name) for name in tools_used)
    if wrote:
        return diagnosis
    return replace(diagnosis, no_write_tool=True, narrated_call=narrated)


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


# A fenced block holding the whole document, as a model writes one when it is
# narrating a tool call rather than making one: ```markdown ... ``` or ``` ... ```.
# The opening fence of a narrated document, and everything after it. Where it
# ends is decided by `_fenced_documents` counting fences rather than by the
# regex, because neither greediness is right on its own.
_FENCE_OPEN = re.compile(r"```(?:markdown|md)?\n", re.MULTILINE)
# Horizontal whitespace only, so the match starts on the fence's own line: a
# plain `\s*` also spans the newline before it, and `_is_bare` then reads the
# blank line above instead of the fence.
_FENCE = re.compile(r"^[^\S\n]*```", re.MULTILINE)


def _fenced_documents(text: str) -> list[str]:
    """Documents inside ``` fences, each closed at its own matching fence.

    Fences are counted rather than matched by a regex, because both
    greediness settings are wrong against a real review. The format this
    pipeline generates nests fenced evidence blocks inside its findings, so a
    lazy `.*?` closes on the first *inner* fence and truncates the document
    at its first code sample. A greedy `.*` instead runs to the last fence in
    the whole reply, swallowing any commentary the model added after the
    document — which arrives in the review file as a stray fence followed by
    chatter, a worse artifact than the truncation it was meant to fix.

    Depth is what distinguishes them: an inner fence opens a block and the
    next one closes it, so only a fence at depth zero ends the document.
    """
    documents = []
    position = 0
    while (opening := _FENCE_OPEN.search(text, position)) is not None:
        body = text[opening.end():]
        end = _document_end(body)
        documents.append(body if end is None else body[:end])
        if end is None:
            break
        # Resume past this document's closing fence, never inside it, so the
        # nested blocks of a document already taken are not re-read as
        # documents of their own. `_same_document` would drop them anyway —
        # they carry a different heading, or none — but only after the whole
        # text had been rescanned once per nested block.
        position = opening.end() + end
    return _same_document(documents)


def _same_document(documents: list[str]) -> list[str]:
    """The blocks that are drafts of the first one, dropping later commentary.

    The caller keeps the last candidate, because a document redrafted after a
    refused write grows across attempts. A reply that finishes its review and
    then adds a second fenced block of commentary is the other shape that
    reaches here, and under that rule the commentary wins.

    A redraft repeats the document's own title; commentary is a different
    document with a different one. Comparing the first heading line separates
    them where length or position cannot — a redraft may be shorter than the
    draft it replaces, and both shapes put the extra block last.

    Untitled blocks cannot be compared this way, so a document with no heading
    at all is dropped: the caller's own gate would reject it anyway, and
    keeping it here lets it outrank a real document that came before it.
    """
    titles = [_first_heading_line(doc) for doc in documents]
    titled = [(doc, title) for doc, title in zip(documents, titles) if title]
    if not titled:
        return []
    first_title = titled[0][1]
    return [doc for doc, title in titled if title == first_title]


# The tail a narrated XML-shaped call leaves after the document:
# `</content></write>`, `</parameter></invoke>`, and the like. Closing tags
# only — a bare `"` or `}` is not matched, because those are also how a
# document legitimately ends (a quoted line, a code sample's last brace) and
# there is no way to tell the two apart from the tail alone.
_CALL_TAIL = re.compile(r"""(?:\s*</[A-Za-z_][\w.-]*>)+\s*$""")


def _strip_call_syntax(text: str) -> str:
    """Drop the closing tags of an XML-shaped call written out as text.

    A narrated call wraps the document, so trimming the preamble off the
    front leaves the call's own tail on the end. For the XML shape that tail
    is unambiguous — no markdown document ends in `</content></write>` — and
    it is visible junk in the recovered review.

    The JSON shape is handled by `_json_call_content` before this, because its
    tail cannot be stripped safely: `"` and `}` are both ways a real document
    ends, and its body needs unescaping rather than trimming anyway.
    """
    return _CALL_TAIL.sub("", text).rstrip()


def _first_heading_line(text: str) -> str:
    """The text of the first heading line, or "" when there is none."""
    match = _HEADING.search(text)
    if not match:
        return ""
    line_end = text.find("\n", match.start(1))
    return text[match.start(1):line_end if line_end != -1 else len(text)].strip()


def _document_end(body: str) -> int | None:
    """Where the document's own closing fence starts, or None if it never closes.

    Every fence toggles depth, so a nested block's opening and closing pair
    cancel and only a fence met at depth zero ends the document. Tracking the
    toggle is what separates the two — the inner block's *closing* fence is
    bare and column-zero exactly like the document's, so nothing about the
    line itself distinguishes them.

    None is an unclosed fence: the reply ended mid-document, and what there is
    of it is still the whole of what the agent produced.
    """
    depth = 0
    for fence in _FENCE.finditer(body):
        if depth == 0 and _is_bare(body, fence):
            return fence.start()
        depth = 0 if _is_bare(body, fence) else 1
    return None


def _is_bare(body: str, fence: re.Match) -> bool:
    """Whether this fence is a bare ``` rather than one opening a tagged block."""
    line_end = body.find("\n", fence.start())
    line = body[fence.start():line_end if line_end != -1 else len(body)]
    return line.strip() == "```"


def _narrated_write_contents(records: list[dict]) -> list[str]:
    """Documents an agent typed into its reply instead of calling a tool with.

    A third lost-write shape, and the one neither other reader sees. The model
    ends its turn having produced the whole document as assistant text — some
    runs preface it with the tool call spelled out in prose, others emit XML
    that looks like a tool call — so the run reaches `agent_end` with the
    findings present and no tool ever invoked. Both other sources key off a
    record only a real call writes, so they return nothing and a complete
    review is discarded.

    Observed against `claude-sonnet-5` on Vertex under `--mode rpc`: three
    consecutive review runs ended this way, one of them with six such blocks
    and zero `tool_execution_start` records. It is intermittent rather than
    deterministic — a fourth run on the same prompt and argv called its tools
    normally — which is what makes salvage the right answer instead of a
    prompt change: there is nothing to fix in the input, and the document is
    sitting in the log.

    Fenced content is preferred, since a model that narrates a write usually
    puts the document in a block. The bare text is the fallback, which the
    heading filter in the caller is what makes safe: ordinary commentary does
    not contain a markdown heading, and a reply that does is the deliverable.
    """
    contents = []
    for record in records:
        for message in _assistant_messages(record):
            contents.extend(_text_documents(message))
    return contents


def _text_documents(message: dict) -> list[str]:
    """Every candidate document in one assistant message's text blocks."""
    contents = []
    for block in message.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text") or ""
        if (decoded := _json_call_content(text)) is not None:
            contents.append(decoded)
            continue
        fenced = _fenced_documents(text)
        # Trimmed on both branches: a fenced document can still carry the
        # narration's own preamble inside the fence, and a bare one usually
        # does.
        contents.extend(
            _strip_call_syntax(_from_first_heading(c))
            for c in (fenced or [text])
        )
    return contents


def _json_call_content(text: str) -> str | None:
    """The `content` of a JSON-shaped tool call written out as text, decoded.

    A model narrating in JSON emits the document as a *string literal*, so its
    newlines arrive as a backslash and an `n`. Recovered verbatim that is one
    long line with `\\n` through it rather than a markdown document, which no
    amount of trimming fixes — it has to be decoded.

    Only a reply that is entirely one JSON object qualifies, and only when it
    carries a `content` string: a reply merely containing a JSON snippet is a
    document that quotes JSON, not a narrated call. None means "not this
    shape", leaving the text to the other readers.
    """
    stripped = text.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return None
    try:
        call = json.loads(stripped)
    except ValueError:
        return None
    content = call.get("content") if isinstance(call, dict) else None
    return content if isinstance(content, str) and content.strip() else None


# A markdown heading, capturing its `#` run so the level can be compared.
# An opening quote may sit in front of it: a narrated call puts the title
# straight after `write review.md "`, where a line-anchored search would miss
# it and take the first heading on a line of its own instead — dropping the
# document's title and the summary under it.
_HEADING = re.compile(r"""(?:^|["'])((#{1,6}) )""", re.MULTILINE)


def _from_first_heading(text: str) -> str:
    """`text` from its top-level heading on, or unchanged when it has none.

    A narrated write is the document with a sentence of preamble in front of
    it — "I already wrote the file, let me re-issue it" — and often the tool
    call spelled out as prose. Recovering that verbatim puts the chatter in
    the review file, where the heading is what every later reader and the
    archive parser key off.

    "Top-level" means the shallowest heading level the text contains, not a
    fixed `#`. Both fixed choices are wrong against a deliverable this
    pipeline actually generates: matching any level lets a model that titles
    its own narration (`## My plan`) keep the chatter, while matching only
    level 1 stops trimming entirely for the scout artifact, whose format
    starts at `## Investigation Leads` and has no level-1 title at all. The
    shallowest level is the document's own outline root either way, and
    narration that happens to use a *shallower* heading than the document is
    the one shape this does not catch.

    Only the leading text is dropped, never a trailing word: an agent that
    stopped mid-document is a partial review worth keeping, and there is no
    marker that reliably says where one ends. Text with no heading is returned
    as-is, and the caller's own heading filter is what then rejects it.
    """
    headings = [
        (match.start(1), len(match.group(2)))
        for match in _HEADING.finditer(text)
    ]
    if not headings:
        return text
    top = min(level for _, level in headings)
    return next(text[pos:] for pos, level in headings if level == top)


def _assistant_messages(record: dict) -> list[dict]:
    """The assistant messages a log record carries, in either shape.

    A `turn_end` holds one under `message`; an `agent_end` holds a list under
    `messages`, which includes the user turn that prompted it. Only the
    assistant's own text is a candidate — the user half is the prompt, and it
    carries headings of its own.
    """
    candidates = record.get("messages")
    if not isinstance(candidates, list):
        one = record.get("message")
        candidates = [one] if isinstance(one, dict) else []
    return [
        m for m in candidates
        if isinstance(m, dict) and m.get("role") == "assistant"
    ]


def try_recover_output(log_path: str, output_path: str) -> bool:
    """Salvage a document the agent wrote but that never reached `output_path`.

    Three sources, because a lost write has three shapes: a Claude denial
    carries the content in its `permission_denials` record, a Pi write carries
    it in the `tool_execution_start` that announced it, and a narrated write
    carries it in assistant text with no tool call at all.

    Public because `agent.retry` runs this before writing a run off as
    unproductive — the content is in the log either way.

    The last qualifying candidate wins. An agent refused its first write tries
    again, and the document grows across those attempts rather than shrinking;
    taking the first would recover a draft and discard the review.

    Narrated text is therefore ordered *first*, not last, which is the reverse
    of how it reads: the scan below runs from the end, so the earliest entry is
    the weakest claim. A real write attempt is better evidence than a reply
    that merely looks like one, so narration only wins when neither other
    source produced a candidate that qualifies.
    """
    if not log_path or not Path(log_path).is_file():
        return False
    records = read_jsonl(log_path)
    candidates = (
        _narrated_write_contents(records)
        + _collect_denied_contents(records)
        + _pi_write_attempts(records, output_path)
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
