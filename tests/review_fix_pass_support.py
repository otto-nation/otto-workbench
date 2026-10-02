"""Helpers shared by the review fix pass suites: the real-repo worktree, the job
and checklist builders, and the stubbed agent that answers the checklist.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import agent.invoke
import fix.engine
import git.push
import review.fix
from pr.fix import FixOutcome, ItemOutcome
from gh.types import PRContext, PRMetadata
from review.types import Finding, ReviewJob
import fix.verify

# What the push owner answers when the fix pass's commit reached the remote.
# The pass no longer pushes for itself — `land` does — so stubbing the owner is
# how a test keeps a real commit and no network.
_PUSHED = git.push.PushResult(
    git.push.PushStatus.PUSHED, sha="9bc3f64ab", branch="feat/x", remote_sha="9bc3f64ab",
)


@pytest.fixture
def git_wt(tmp_path):
    """A real repo with one commit — the fix pass's staging is git behaviour."""
    wt = tmp_path / "worktree"
    wt.mkdir()
    # Empty hooks dir: the developer's own `core.hooksPath` is global, so
    # without this the fixture runs their pre-commit hook and the suite passes
    # or fails on whatever that machine has installed.
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    git_out(wt, "init", "-q", "-b", "main")
    git_out(wt, "config", "user.email", "test@example.com")
    git_out(wt, "config", "user.name", "Test")
    git_out(wt, "config", "commit.gpgsign", "false")
    git_out(wt, "config", "core.hooksPath", str(hooks))
    (wt / "src.py").write_text("original\n")
    (wt / ".gitignore").write_text("*.cache\n")
    git_out(wt, "add", "-A")
    git_out(wt, "commit", "-qm", "initial")
    return wt


def _committed_paths(wt: Path) -> set[str]:
    # quotePath=false for the same reason `_changed_source_files` sets it: git
    # escapes a non-ASCII name by default, and the assertion would compare the
    # escaped spelling against the real one.
    out = git_out(
        wt, "-c", "core.quotePath=false",
        "show", "--name-only", "--pretty=format:", "HEAD",
    )
    return {line for line in out.strip().splitlines() if line}


def _make_job(
    git_wt, tmp_path, review_content: str = "", *,
    files: list[str] | None = None, base: str = "main",
) -> ReviewJob:
    """A review whose deliverable is `review_content`, over `git_wt`.

    A real `ReviewJob` rather than a mock: the adapter reads the review's
    effort, model, config and artifact directory off it, and a mock answers
    every one of those with something that is not what a review holds.

    `files` is the branch's changed-file list against `base` — the same
    shape `job.pr.files` carries for a real PR or stacked self-review.
    """
    review_file = tmp_path / "reviews" / "review.md"
    review_file.parent.mkdir(exist_ok=True)
    review_file.write_text(review_content)
    file_dicts = [
        {"path": p, "additions": 1, "deletions": 0} for p in (files or [])
    ]
    return ReviewJob(
        repo="owner/repo", pr_number="42",
        pr=PRMetadata(
            title="feat: thing", body="", head="user/feat/thing", base=base,
            head_sha="abc1234", additions=1, deletions=0,
            changed_files=len(file_dicts), files=file_dicts,
        ),
        ctx=PRContext(commits="abc1234 feat: thing"),
        wt_path=str(git_wt),
        review_file=str(review_file),
        session_log=str(review_file.parent / "session.jsonl"),
    )


def _tracking(job: ReviewJob) -> Path:
    return Path(job.artifact_dir) / fix.engine.TRACKING_FILENAME


def _is_heading(line: str) -> bool:
    return line.startswith("## <!-- fix:")


def _heading_id(line: str) -> str:
    return line.split("fix:")[1].split(" ")[0]


def _answer(job: ReviewJob, boxes: dict[str, str], *, work=None, stop=None):
    """A `run_fix` stub that ticks `boxes` on the checklist it finds on disk.

    Keyed by finding id, valued with the whole box line the agent would leave
    behind — `"fixed"`, or `"declined — why"`. An id left out is an item the
    agent never answered, which is what the engine reads as still owed.

    `work` runs first and is where a test puts the edits the agent would have
    made to the worktree; the engine writes the checklist immediately before
    each invocation, so an answer written any earlier is thrown away.
    `stop` is the diagnosis the real `run_fix` would return for a truncated
    pass, so a test can ask the engine to report one without writing a log.
    """
    tracking = _tracking(job)

    def run_fix(_phase, _prompt, **_kwargs):
        if work:
            work()
        text = tracking.read_text()
        owed = {_heading_id(ln) for ln in text.splitlines() if _is_heading(ln)} & set(boxes)
        item = ""
        out: list[str] = []
        for line in text.splitlines(keepends=True):
            if _is_heading(line):
                item = _heading_id(line)
            answer = boxes.get(item, "")
            label = answer.split(" — ")[0]
            if answer and line.startswith(f"- [ ] {label}"):
                line = f"- [x] {answer}\n"
                owed.discard(item)
            out.append(line)
        tracking.write_text("".join(out))
        # A label this checklist has no box for ticks nothing, and the engine
        # reads the silence as a deferral — which several assertions here would
        # take for the answer they asked for. Fail on the typo instead.
        assert not owed, f"no box matched the answer for: {sorted(owed)}"
        if stop is None:
            return agent.invoke.FixResult(0, None)
        return agent.invoke.FixResult(0, None, stop=stop)

    return run_fix


def _run(
    job: ReviewJob, boxes: dict[str, str], *,
    work=None, verdicts=None, stop=None, **kwargs,
):
    """Run the pass with the agent stubbed, and hand back the stub.

    The verify gate is stubbed alongside it, at `fix.verify.run` rather than at
    the agent: the two share one `run_fix`, so a single stub would hand the
    gate's checklist to a helper that answers in the fix pass's vocabulary and
    every case here would fail on a box the verify file does not carry.

    `verdicts` defaults to none at all, which the engine reads as a gate that
    answered nothing — unverified, still fixed, still committed. That keeps the
    cases below about what this module decides; the gate's own effect on them is
    `TestTheVerifyGate`'s.
    """
    with patch.object(fix.verify, "run",
                      side_effect=lambda *a, **k: dict(verdicts or {})):
        with patch.object(agent.invoke, "run_fix",
                          side_effect=_answer(job, boxes, work=work, stop=stop)) as inv:
            review.fix.run_fix_pass(job, **kwargs)
    return inv


def _outcome(item_id: str, outcome: FixOutcome, reason: str = "", **kwargs) -> ItemOutcome:
    return ItemOutcome(id=item_id, outcome=outcome, reason=reason, **kwargs)


def _finding(fid: str, path: str = "a.py", body: str = "body", **kwargs) -> Finding:
    return Finding(
        id=fid, severity=fid[0], seq=int(fid[1:]), path=path,
        line=1, end_line=None, body=body, **kwargs,
    )
