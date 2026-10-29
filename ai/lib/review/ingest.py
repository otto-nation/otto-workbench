"""One ingest boundary: an agent's JSON artifact becomes typed data, once.

Every agent output that becomes review state passes through `load_artifact`
where it enters: `json.loads`, then the strict schema (`core.strict`), then the
artifact's own `problems()` (`review.artifacts`). Ingest is strict. Invalid
JSON (named by line and column), a schema violation or a malformed entry (named
by JSON path) is an `IngestError`, never a silent repair. `ingest_artifact`
gives the agent one turn to fix a rejection and returns an `ArtifactOutcome`;
a second rejection is an agent failure, handled as failures already are.

`review.md` is rendered from the record and never parsed back; nothing here
reads markdown.
"""

# doc-group: findings

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Generic, Protocol, TypeVar

import core.log
import core.strict
from agent.diagnosis import Diagnosis, DiagnosisKind


class IngestErrorKind(StrEnum):
    UNKNOWN_SEVERITY = "unknown_severity"
    DUPLICATE_ID = "duplicate_id"
    INVALID_JSON = "invalid_json"
    SCHEMA_VIOLATION = "schema_violation"
    EMPTY_FIELD = "empty_field"
    LINE_IN_PATH = "line_in_path"
    BAD_LOCATION = "bad_location"
    BAD_CARRY = "bad_carry"
    FOCUS_COUNT = "focus_count"
    FOCUS_SIZE = "focus_size"


@dataclass(frozen=True)
class IngestError:
    """One entry ingest will not represent, and where it is.

    A JSON decode error names `line` and `column`. A schema or semantic error
    names the JSON path `at`, because a parsed document has no lines left to
    point at.
    """

    kind: IngestErrorKind
    line: int
    detail: str
    column: int = 0
    at: str = ""

    def render(self) -> str:
        where = self.at or (
            f"line {self.line}, column {self.column}" if self.column else f"line {self.line}"
        )
        return f"{where}: {self.kind.value} — {self.detail}"


class IngestRejected(Exception):
    """The text holds entries ingest will not represent; `errors` names each."""

    def __init__(self, errors: tuple[IngestError, ...]) -> None:
        self.errors = errors
        super().__init__("; ".join(e.render() for e in errors))


# ceiling: a fixed repair budget rather than one scaled to the error count.
# Upgrade when repair turns regularly end with errors left that a larger budget
# would have reached — the trail records every second rejection.
REPAIR_MAX_TURNS = 10


class Artifact(Protocol):
    """An agent artifact type: strict-loadable, and able to name its own problems."""

    def problems(self) -> tuple[IngestError, ...]: ...


A = TypeVar("A", bound=Artifact)


@dataclass(frozen=True)
class ArtifactOutcome(Generic[A]):
    """What ingesting an agent's JSON produced, after at most one repair turn.

    `ok` is whether an artifact was built. `repaired` is whether the repair
    turn ran. `diagnosis` is the repair invoke's own failure — a non-zero exit
    or a cause `diagnose` named — and is unset when the repair ran and ingest
    still rejected the file.
    """

    artifact: A | None
    errors: tuple[IngestError, ...] = ()
    repaired: bool = False
    diagnosis: Diagnosis | None = None

    @property
    def ok(self) -> bool:
        return self.artifact is not None


def _decode_error(text: str, exc: json.JSONDecodeError) -> IngestError:
    detail = exc.msg
    if text.lstrip().startswith("```"):
        detail += " — the file starts with a code fence; write only the JSON document"
    return IngestError(IngestErrorKind.INVALID_JSON, exc.lineno, detail, column=exc.colno)


def load_artifact(path: str, kind: type[A]) -> A:
    """The artifact the JSON at `path` holds. Raises `IngestRejected` when it is malformed.

    Three gates, in order, each one only reached when the last passed:
    `json.loads` (line and column), the strict schema (JSON path), and the
    artifact's own `problems()` (JSON path).
    """
    text = Path(path).read_text()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IngestRejected((_decode_error(text, exc),)) from None
    try:
        artifact = core.strict.load_strict(kind, raw)
    except core.strict.SchemaRejected as exc:
        raise IngestRejected(tuple(
            IngestError(IngestErrorKind.SCHEMA_VIOLATION, 0, v.detail, at=v.at)
            for v in exc.violations
        )) from None
    problems = artifact.problems()
    if problems:
        raise IngestRejected(problems)
    return artifact


def repair_prompt(prompt: str, path: str, errors: tuple[IngestError, ...]) -> str:
    """Re-send `prompt` with the ingest errors the agent must fix in `path`."""
    listed = "\n".join(f"- {e.render()}" for e in errors)
    return (
        f"{prompt}\n\n## Your output was rejected\n\n"
        f"What you wrote to `{path}` cannot be read:\n\n{listed}\n\n"
        "Fix that file in place — only what is named above — so it is the format "
        "the Output section asks for. Do not rewrite anything else."
    )


def ingest_artifact(
    path: str, prompt: str, invoke: Callable[[str, int], int], kind: type[A],
    *, diagnose: Callable[[], Diagnosis] | None = None,
) -> ArtifactOutcome:
    """Load the agent's JSON at `path` as a `kind`, giving the agent one turn to fix a rejection.

    A second rejection is returned, not raised. A repair turn that itself
    fails — a non-zero exit, or a cause `diagnose` names — is that failure,
    not a malformed-output rejection.
    """
    try:
        return ArtifactOutcome(load_artifact(path, kind))
    except IngestRejected as exc:
        errors = exc.errors
    core.log.warn(f"{path} rejected ({len(errors)} error(s)) — one repair turn")
    rc = invoke(repair_prompt(prompt, path, errors), REPAIR_MAX_TURNS)
    try:
        return ArtifactOutcome(load_artifact(path, kind), repaired=True)
    except IngestRejected as exc:
        if rc == 0:
            return ArtifactOutcome(None, exc.errors, repaired=True)
        failure = diagnose() if diagnose is not None else Diagnosis(
            DiagnosisKind.AGENT_ERROR, detail=f"exited with code {rc}",
        )
        return ArtifactOutcome(None, repaired=True, diagnosis=failure)
