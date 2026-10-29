"""Builders the convergence suites share: records, a committed repo, a job.

Records are built from fields, never by ingesting markdown, so these suites
survive the review-record schema moving to agent-written JSON.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from conftest import commit_all, git_out, init_repo  # noqa: E402
from core.phases import Mode  # noqa: E402
from gh.types import PRContext, PRMetadata  # noqa: E402
from review.record import RecordFinding, ReviewRecord  # noqa: E402
from review.types import ReviewJob  # noqa: E402

def finding(fid: str, body: str = "body", *, path: str = "", line: int | None = None,
            **changes) -> RecordFinding:
    """A finding at *path*:*line*, with a stable ID of its own unless one is given.

    The stable ID is opaque to everything these suites exercise — carry-forward
    and reconciliation compare it, nothing re-derives it — so a fixed per-ID
    value stands in for the hash ingest would compute.
    """
    changes.setdefault("stable_id", f"sid{fid.lower()}")
    return RecordFinding(id=fid, severity=fid[0], path=path, line=line, body=body, **changes)


def committed_repo(path: Path, files: dict[str, str]) -> Path:
    repo = init_repo(path)
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    commit_all(repo, "base")
    return repo


def head(repo: Path) -> str:
    return git_out(repo, "rev-parse", "HEAD").strip()


def review_job(wt: Path, review_dir: Path, *, mode: Mode = Mode.SELF, head_sha: str = "",
               files=(), prior=None, preflight=None) -> ReviewJob:
    review_dir.mkdir(parents=True, exist_ok=True)
    job = ReviewJob(
        repo="o/r", pr_number="", ctx=PRContext(), mode=mode,
        pr=PRMetadata("t", "", "feat", "main", head_sha, 1, 0, len(files),
                      [{"path": p, "additions": 1, "deletions": 0} for p in files]),
        wt_path=str(wt), review_file=str(review_dir / "review.md"),
        session_log=str(review_dir / "session.jsonl"),
    )
    job.prior = prior
    job.preflight = preflight
    return job


def merged(record: ReviewRecord | None = None, fresh: int | None = None):
    """A `review.steps.Merged` of *record*, counting `fresh` new findings.

    The one place a suite builds one, so a field `Merged` grows changes this
    body and no test.
    """
    import review.steps
    return review.steps.Merged(record if record is not None else ReviewRecord(), fresh=fresh)


def single_agent_record(monkeypatch, record: ReviewRecord) -> None:
    """Make `review.pipeline.run_single_agent` read *record* as the agent's review.

    The one place a suite reaches into how the single-agent path turns the
    agent's file into a record: the artifact ingest returns builds *record*.
    """
    from types import SimpleNamespace

    import review.pipeline
    from review.ingest import ArtifactOutcome
    artifact = SimpleNamespace(to_record=lambda **_: record, focus=())
    monkeypatch.setattr(review.pipeline, "ingest_artifact",
                        lambda *a, **k: ArtifactOutcome(artifact))
