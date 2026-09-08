"""What a failed CI job was actually complaining about, as `FailureItem`s.

GitHub's own account of a failure is an annotation, and an annotation is often
useless: "Process completed with exit code 1" pinned to a line of the workflow
file. This module is the ladder down from that — annotations first, then the
job's logs, then the test-results artifact — stopping at the first source that
says something a fix pass could act on. `fetch_job_failure` is the whole ladder
for one job and is the only thing outside here that needs calling.

Classifying the text once it has been found belongs to `pr.ci_failures`, and
getting it belongs to `gh.run_reads`. What is here is the decision of which
source to believe.
"""

# doc-group: publishing

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from agent.session import read_jsonl
from core.text import slugify
from gh import run_reads
from pr import ci_failures as ci

_GENERIC_MESSAGES = (
    "process completed with exit code",
    "a]process completed with exit code",  # GHA bracket-notation artifact
    "check failure on line",
    "failed with exit code",
    "exited with code",
    "returned a non-zero code",
)


# ── Annotations → items ──────────────────────────────────────────────────────

def build_failure_id(annotation: dict, job_name: str) -> str:
    """A failure's identity, stable across runs so progression can track it.

    A title is preferred over the message digest for a pathless annotation
    because a title reads as something in the report. The digest behind it is
    `hashlib`'s rather than the builtin `hash`, whose string seed is randomised
    per process: an id built that way never matches the same failure in the next
    run, so progression would report it as new every time.
    """
    path = annotation.get("path", "")
    line = annotation.get("start_line", 0)
    title = annotation.get("title", "")
    if path and line:
        slug = title.split(":")[0].strip().lower() if title else "err"
        return f"{slug}-{path}-{line}"
    if title:
        return f"{job_name}-{slugify(title)}"
    message = annotation.get("message", "")
    digest = hashlib.sha256(message.encode("utf-8", "replace")).hexdigest()[:8]
    return f"{job_name}-{digest}"


def annotations_to_items(
    annotations: list[dict], job_name: str, source_run_id: int | None = None,
    context: str | None = None,
) -> list[ci.FailureItem]:
    """Convert raw annotations to FailureItems, skipping notices."""
    items: list[ci.FailureItem] = []
    for ann in annotations:
        if ann.get("annotation_level") == "notice":
            continue
        item_id = build_failure_id(ann, job_name)
        # An empty message is no message, the same reading build_failure_id
        # takes of it — an annotation carrying only a title reports the title.
        text = ann.get("message") or ann.get("title", "")
        items.append(ci.FailureItem(
            id=item_id,
            annotation=text,
            file=ann.get("path") or None,
            line=ann.get("start_line") or None,
            diagnosis=None,
            fix_sha=None,
            outcome=None,
            headline=ci.extract_headline(text) or ci.extract_headline(context),
            source_run_id=source_run_id,
            context=context,
        ))
    return items


def _fallback_item(job_name: str, source_run_id: int | None = None) -> ci.FailureItem:
    """Create a placeholder item for a failed job with no annotations."""
    return ci.FailureItem(
        id=f"{job_name}-no-annotation",
        annotation=f"Job '{job_name}' failed with no annotations",
        file=None,
        line=None,
        diagnosis=None,
        fix_sha=None,
        outcome=None,
        source_run_id=source_run_id,
    )


def annotations_uninformative(annotations: list[dict]) -> bool:
    """True when non-notice annotations carry no actionable diagnostic context.

    A generic message (exit code restated, check failure marker) is always
    uninformative regardless of path — the real error is in the logs.
    """
    non_notices = [a for a in annotations if a.get("annotation_level") != "notice"]
    if not non_notices:
        return True
    for a in non_notices:
        msg = (a.get("message", "") or "").lower()
        if any(g in msg for g in _GENERIC_MESSAGES):
            continue
        path = a.get("path", "")
        has_source_path = bool(path) and "." in path.rsplit("/", 1)[-1]
        if has_source_path:
            return False
    return True


# ── Log and artifact fallbacks ───────────────────────────────────────────────

@dataclass(frozen=True)
class LogFallback:
    """What a job's logs yielded once its annotations turned out to be too thin.

    `structured` is the distinction the caller acts on: one annotation per
    failure, as the log itself delimited them, is worth more than whatever
    GitHub attached to the failed step — which for a generic step failure is
    the workflow file and a line number in the log. An unstructured extraction
    is one window over the whole run and only good for context, so it leaves
    the original annotations in place.
    """
    annotations: list[dict]
    context: str
    kind: ci.FailureKind
    structured: bool

    @property
    def ok(self) -> bool:
        return bool(self.annotations)


def _test_annotation(failure: ci.TestFailure) -> dict:
    """One test failure in the annotation shape `annotations_to_items` reads.

    A located failure withholds the title so its id stays `err-<path>-<line>`;
    the test name is the first line of the message either way. An unlocated one
    passes the name, which is the only stable thing left to key it on.
    """
    if not failure.location:
        return {"message": failure.context, "path": "", "start_line": 0, "title": failure.name}
    return {
        "message": failure.context,
        "path": failure.location.file,
        "start_line": failure.location.line,
        "title": "",
    }


def log_fallback(repo: str, job: dict, run_data: dict, kind: ci.FailureKind) -> LogFallback:
    """Fetch job logs and extract failure context as synthetic annotations.

    A log in a format `ci.extract_test_failures` parses becomes one annotation
    per failing test rather than one window over the whole run, so a suite with
    several failures surfaces as several items and each carries the location
    the suite itself printed for it.
    """
    job_id = job.get("databaseId", 0)
    log_text = run_reads.fetch_job_logs(repo, job_id) if job_id else ""
    if not log_text:
        source_run_id = job.get("_source_run_id", run_data["databaseId"])
        log_text = run_reads.fetch_failed_logs(repo, source_run_id)
    if not log_text:
        return LogFallback([], "", kind, structured=False)

    job_name = job.get("name", "unknown")
    tests = ci.extract_test_failures(log_text)
    if tests:
        context = "\n\n".join(f.context for f in tests)
        return LogFallback(
            [_test_annotation(f) for f in tests], context,
            ci.classify_job(job_name, [context]),
            structured=True,
        )

    context = ci.extract_failure_context(log_text, kind)
    if not context:
        return LogFallback([], "", kind, structured=False)
    annotations = [{"message": context, "path": "", "start_line": 0, "title": ""}]
    return LogFallback(annotations, context, ci.classify_job(job_name, [context]), structured=False)


def parse_test_artifact(artifact_dir: str) -> str:
    """Failure output read out of a downloaded test-results artifact directory."""
    jsonl_files = sorted(Path(artifact_dir).glob("*.json"))
    if not jsonl_files:
        return ""
    entries = read_jsonl(jsonl_files[0])
    fail_packages = {e.get("Package", "") for e in entries if e.get("Action") == "fail"}
    if not fail_packages:
        return ""
    output_lines = [
        e.get("Output", "").rstrip()
        for e in entries
        if e.get("Action") == "output" and e.get("Package") in fail_packages
    ]
    if not output_lines:
        return ""
    return ci.extract_failure_context("\n".join(output_lines), ci.FailureKind.TEST)


def _artifact_name(job_name: str) -> str:
    """The `test-results-*` artifact a job of this name uploads."""
    slug = job_name.lower()
    for prefix in ("test: ", "test "):
        if slug.startswith(prefix):
            slug = slug[len(prefix):]
            break
    return f"test-results-{slug.replace(' ', '-')}"


def fetch_test_artifact(repo: str, run_id: int, job_name: str) -> str:
    """Download this job's test-results artifact and read the failure out of it."""
    with run_reads.download_artifact(repo, run_id, _artifact_name(job_name)) as artifact_dir:
        return parse_test_artifact(artifact_dir) if artifact_dir else ""


# ── One job's failure ────────────────────────────────────────────────────────

def extract_failed_step(job: dict) -> str | None:
    """Return the name of the first failed step in a job, if available."""
    for step in job.get("steps", []):
        if step.get("conclusion") in run_reads.FAILURE_CONCLUSIONS:
            return step.get("name")
    return None


@dataclass(frozen=True)
class JobFailure:
    """Everything one failed job contributes to a run's `FailureGroup`."""
    job_name: str
    kind: ci.FailureKind
    items: list[ci.FailureItem]
    failed_step: str | None


def _make_result(job_name, kind, annotations, source_run_id, failed_step, context=None):
    """Build the JobFailure from resolved annotations."""
    if annotations:
        items = annotations_to_items(annotations, job_name, source_run_id, context=context)
    else:
        items = [_fallback_item(job_name, source_run_id)]
    if not items:
        return None
    return JobFailure(job_name=job_name, kind=kind, items=items, failed_step=failed_step)


def fetch_job_failure(repo: str, job: dict, run_data: dict) -> JobFailure | None:
    """Fetch annotations/logs for a single failed job. Thread-safe."""
    job_name = job.get("name", "unknown")
    job_id = job.get("databaseId", 0)
    source_run_id = job.get("_source_run_id") or run_data.get("databaseId")
    failed_step = extract_failed_step(job)

    annotations = run_reads.fetch_annotations(repo, job_id) if job_id else []
    annotation_texts = [a.get("message", "") for a in annotations]
    kind = ci.classify_job(job_name, annotation_texts)

    if kind not in (ci.FailureKind.BUILD, ci.FailureKind.TEST):
        return _make_result(job_name, kind, annotations, source_run_id, failed_step)

    if annotations and not annotations_uninformative(annotations):
        return _make_result(job_name, kind, annotations, source_run_id, failed_step)

    # Annotations absent or uninformative — try logs, then artifact
    have_original = bool(annotations)
    fallback = log_fallback(repo, job, run_data, kind)

    # A location the log reported beats the one GitHub attached to the failed
    # step, which for a generic step failure anchors on the workflow file. The
    # annotations carry their own context here, so nothing below is needed.
    if fallback.structured:
        return _make_result(job_name, fallback.kind, fallback.annotations, source_run_id, failed_step)

    ctx = fallback.context if fallback.ok else fetch_test_artifact(repo, source_run_id, job_name)
    if ctx and have_original:
        return _make_result(job_name, fallback.kind if fallback.ok else kind, annotations, source_run_id, failed_step, context=ctx)
    if ctx:
        annotations = [{"message": ctx, "path": "", "start_line": 0, "title": ""}]
        kind = ci.classify_job(job_name, [ctx])
    return _make_result(job_name, kind, annotations, source_run_id, failed_step)
