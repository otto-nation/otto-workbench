"""The synthesis eval task: merge planted group findings, score what survives.

Synthesis turns the groups' findings into the review a reader sees, and its two
errors cost differently. Dropping a real finding hides a defect; keeping a
duplicate only costs the reader a line. A case therefore holds a small repo and
the findings its groups wrote, each marked `keep` (a distinct finding the review
must carry) or `drop` (a duplicate of another finding). The agent is shown them as
production shows them: the merged group JSON, in the production template.

| Score field | Meaning here |
|---|---|
| `recall` | Share of the `drop` findings the review no longer carries — the noise removed |
| `precision` | Share of the `keep` findings the review still carries — 1.0 means nothing real was lost |

A finding is matched in the output by its `sid`, which the template tells the
agent to keep, and failing that by its path and line. Findings the agent adds
(cross-cutting ones) match nothing planted and are not scored. A review that
could not be read carries nothing, so it scores recall 1.0 and precision 0.0: it
removed all the noise and all the signal, which is the error that matters.

What the eval does not exercise is the pipeline around the phase: the group
agents, the prior-review ledger, and the programmatic verification that runs
after synthesis writes.
"""

# doc-group: eval

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import agent.backend
import agent.phases
import agent.session
import agent.templates
import agent.usage
from agent.registry import PHASES
from agent.types import EFFORT_PRESETS
from core.phases import Effort, Mode, Phase
from eval.scoring import ScoringResult
import eval.task
from eval.task import RunArtifacts, RunOptions, create_temp_repo, outcome_for
from review.artifacts import AgentFinding, ReviewArtifact, output_format
from review.ingest import IngestRejected, load_artifact
from review.prompt import VERDICT_OPTIONS
from review.prompt_sections import _build_focus_section
from review.record import RecordFinding, ReviewRecord
from review.steps import Merged

KEEP = "keep"
DROP = "drop"
_EXPECTS = (KEEP, DROP)

_HOLISTIC = "## Phase 1 context\n_No holistic assessment available._"


@dataclass(frozen=True)
class Planted:
    """One finding a group wrote and what the review should do with it."""

    id: str
    sid: str
    expect: str
    path: str
    line: int
    body: str
    evidence: str = ""
    end_line: int | None = None

    def record(self) -> RecordFinding:
        return RecordFinding(
            id=self.id, severity=self.id[:1], path=self.path, line=self.line,
            end_line=self.end_line, body=self.body, evidence=self.evidence,
            sid=self.sid,
        )


def parse_findings(manifest: dict) -> list[Planted]:
    """The manifest's findings. Raises `ValueError` for one a run could not score."""
    out = []
    for raw in manifest.get("findings", []):
        planted = Planted(
            id=raw["id"], sid=raw["sid"], expect=raw["expect"], path=raw["path"],
            line=raw["line"], body=raw["body"], evidence=raw.get("evidence", ""),
            end_line=raw.get("end_line"),
        )
        if planted.expect not in _EXPECTS:
            raise ValueError(f"{planted.id}: expect must be one of {_EXPECTS}, got {planted.expect!r}")
        out.append(planted)
    for key in ("id", "sid"):
        if len({getattr(p, key) for p in out}) != len(out):
            raise ValueError(f"finding {key}s must be unique")
    if not out:
        raise ValueError("a synthesis case needs at least one finding")
    return out


def build_prompt(planted: list[Planted], output_path: str, repo_dir: str, max_turns: int) -> str:
    """The production synthesis prompt over *planted*, in PR mode."""
    merged = Merged(ReviewRecord(findings=tuple(p.record() for p in planted)))
    block = agent.templates.build_output_block(output_path)
    return agent.templates.render(
        PHASES[Phase.SYNTHESIS].template_for(Mode.PR),
        pr_number="1", repo="eval/corpus", pr_title="eval", pr_header="**PR #1: eval**",
        state_context="", reviews_section="", reply_threads="", prior_section="",
        preflight_data="", delta_section="", issue_section="",
        focus_section=_build_focus_section((), derive=True),
        holistic_content=_HOLISTIC, merged_content=merged.text,
        group_count="2", verdict_options=VERDICT_OPTIONS, wt_path=repo_dir,
        execution_claim_guard=agent.templates.build_execution_claim_guard(8),
        output_block=(
            f"{block}\n\n"
            f"{output_format(ReviewArtifact, Mode.PR, rereview=False, derive_focus=True, has_areas=False)}"
        ),
        max_turns=str(max_turns),
    )


def read_findings(output_path: str) -> list[AgentFinding]:
    """The review's findings. An unreadable file carries none."""
    try:
        return list(load_artifact(output_path, ReviewArtifact).findings)
    except (IngestRejected, OSError):
        return []


def survivors(planted: list[Planted], carried: list[AgentFinding]) -> set[str]:
    """Ids of the planted findings the review still carries.

    By `sid` where the agent kept it, else by an unclaimed finding at the same
    path and line — the template says to keep the sid, and a model that did not
    should lose nothing it still wrote.
    """
    by_sid = {c.sid for c in carried if c.sid}
    found = {p.id for p in planted if p.sid in by_sid}
    unkeyed = [c for c in carried if not c.sid]
    # Keeps claim first: an unkeyed finding must not hide a lost keep behind a drop.
    for p in sorted(planted, key=lambda p: p.expect != KEEP):
        if p.id in found:
            continue
        hit = next((c for c in unkeyed if (c.path, c.line) == (p.path, p.line)), None)
        if hit is not None:
            unkeyed.remove(hit)
            found.add(p.id)
    return found


def score_findings(planted: list[Planted], kept: set[str]) -> tuple[float, float]:
    """`(share of drop findings removed, share of keep findings carried)`."""
    drops = [p for p in planted if p.expect == DROP]
    keeps = [p for p in planted if p.expect == KEEP]
    removed = sum(p.id not in kept for p in drops) / len(drops) if drops else 1.0
    carried = sum(p.id in kept for p in keeps) / len(keeps) if keeps else 1.0
    return removed, carried


class SynthesisTask:
    """Run the synthesis phase over a case's findings and score what it carries."""

    name = "synthesis"
    phase = Phase.SYNTHESIS

    def run(self, case_dir: Path, opts: RunOptions) -> RunArtifacts:
        manifest = json.loads((case_dir / "manifest.json").read_text())
        planted = parse_findings(manifest)
        repo_dir = create_temp_repo(str(case_dir / "src"), prefix="eval-synthesis-")
        log_dir = tempfile.mkdtemp(prefix="eval-synthesis-log-")
        session_log = str(Path(log_dir) / "session.jsonl")
        output = Path(log_dir) / "synthesis.json"
        # The agent's recipe says the file already exists and is empty.
        output.touch()

        settings = eval.task.resolved_settings(self, opts.model)
        effort = Effort(opts.effort)
        max_turns = agent.phases.phase_turns(Phase.SYNTHESIS, effort)
        rc = agent.backend.invoke_agent(agent.backend.AgentInvocation(
            prompt=build_prompt(planted, str(output), repo_dir, max_turns),
            cwd=repo_dir,
            session_log=session_log,
            output_path=str(output),
            add_dirs=agent.session.build_add_dirs(repo_dir, log_dir),
            agent=PHASES[Phase.SYNTHESIS].agent or EFFORT_PRESETS[effort].agent,
            max_turns=max_turns,
            max_budget=agent.phases.phase_budget(Phase.SYNTHESIS, effort),
            model=settings.model,
            thinking=settings.thinking,
            provider=settings.provider,
            compact_at=settings.compact_at,
            phase=str(Phase.SYNTHESIS),
            task="eval-synthesis",
            repo="eval/corpus",
            rules_home=opts.rules_home,
        ))
        usage = agent.usage.parse_session_log(session_log)
        carried = read_findings(str(output))
        kept = survivors(planted, carried)
        return RunArtifacts(
            exit_code=rc, usage=usage, outcome=outcome_for(usage),
            temp_dirs=[repo_dir, log_dir],
            data={
                "findings": planted,
                "kept": sorted(kept),
                "summary": f"carried {len(kept)}/{len(planted)} planted findings"
                + ("" if carried else " (output unreadable or empty)"),
            },
        )

    def score(self, artifacts: RunArtifacts, manifest: dict) -> ScoringResult:
        removed, carried = score_findings(
            parse_findings(manifest), set(artifacts.data.get("kept", ())),
        )
        usage = artifacts.usage
        return ScoringResult(
            entry_name="", model="", run_index=0,
            outcome=artifacts.outcome,
            recall=removed, precision=carried,
            cost_usd=usage.cost, duration_ms=usage.duration_ms,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            billed_input=usage.billed_input,
            cache_read_ratio=usage.cache_read_ratio,
        )
