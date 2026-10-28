"""The disprove eval task: challenge planted findings, score the verdicts.

The disprove gate decides which findings reach the reader, so its two errors
cost differently. Falsifying a real defect hides it; surviving a false one only
leaves noise a reader can dismiss. A case therefore holds a small repo and a
list of findings a reviewer might have written, each marked `real` (it describes
a defect that is in the code) or `false` (the code already handles it, or the
claim misreads it). The gate is shown them rendered exactly as in production and
asked for verdicts.

| Score field | Meaning here |
|---|---|
| `recall` | Share of the `false` findings the gate falsified — the noise it removed |
| `precision` | Share of the `real` findings the gate kept — 1.0 means it hid nothing real |

A finding with no verdict survives, as the template says, so a gate that wrote
nothing scores precision 1.0 and recall 0.0 rather than erroring: the number
then reads as what it was, a gate that challenged nothing. A case with no
finding of one kind scores that field 1.0, since there was nothing to get wrong.
`--compare` gates on a drop in either, so a cheaper model that starts hiding
real findings fails the same way one that drops a defect does in `review`.

Findings are rendered through `review.render.render_body` and the prompt comes
from the production template and output format, so the eval measures the prompt
production sends. What it does not exercise is the review pipeline around the
gate: prompt budgeting and the ingest retry.
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
from core.phases import Effort, Mode, Phase
from eval.scoring import ScoringResult
import eval.task
from eval.task import RunArtifacts, RunOptions, create_temp_repo, outcome_for
from review.artifacts import DisproveArtifact, DisproveOutcome, output_format
from review.ingest import IngestRejected, load_artifact
from review.record import RecordFinding, ReviewRecord
from review.render import render_body

REAL = "real"
FALSE = "false"
_TRUTHS = (REAL, FALSE)


@dataclass(frozen=True)
class CaseFinding:
    """One planted finding and whether it is true."""

    id: str
    truth: str
    path: str
    line: int
    body: str
    evidence: str = ""
    end_line: int | None = None

    def record(self) -> RecordFinding:
        return RecordFinding(
            id=self.id, severity=self.id[:1], path=self.path, line=self.line,
            end_line=self.end_line, body=self.body, evidence=self.evidence,
        )


def parse_findings(manifest: dict) -> list[CaseFinding]:
    """The manifest's findings. Raises `ValueError` for one a run could not score."""
    out = []
    for raw in manifest.get("findings", []):
        finding = CaseFinding(
            id=raw["id"], truth=raw["truth"], path=raw["path"], line=raw["line"],
            body=raw["body"], evidence=raw.get("evidence", ""),
            end_line=raw.get("end_line"),
        )
        if finding.truth not in _TRUTHS:
            raise ValueError(f"{finding.id}: truth must be one of {_TRUTHS}, got {finding.truth!r}")
        if finding.id[:1] not in ("M", "S"):
            raise ValueError(f"{finding.id}: the gate only challenges M and S findings")
        out.append(finding)
    if len({f.id for f in out}) != len(out):
        raise ValueError("finding ids must be unique")
    if not out:
        raise ValueError("a disprove case needs at least one finding")
    return out


def build_prompt(findings: list[CaseFinding], output_path: str, max_turns: int) -> str:
    """The production disprove prompt over *findings*."""
    review = render_body(
        ReviewRecord(findings=tuple(f.record() for f in findings)), evidence=True,
    )
    block = agent.templates.build_output_block(output_path)
    return agent.templates.render(
        PHASES[Phase.DISPROVE].template,
        review_content=review,
        output_block=f"{block}\n\n{output_format(DisproveArtifact, Mode.SELF, rereview=False)}",
        max_turns=max_turns,
    )


def read_verdicts(output_path: str) -> dict[str, DisproveOutcome]:
    """Finding id -> verdict. An unreadable file is no verdicts, so everything survives."""
    try:
        artifact = load_artifact(output_path, DisproveArtifact)
    except (IngestRejected, OSError):
        return {}
    return {v.id: v.outcome for v in artifact.verdicts}


def score_verdicts(
    findings: list[CaseFinding], verdicts: dict[str, DisproveOutcome],
) -> tuple[float, float]:
    """`(falsified share of false findings, kept share of real findings)`."""
    def falsified(f: CaseFinding) -> bool:
        return verdicts.get(f.id) is DisproveOutcome.FALSIFIED

    false_ones = [f for f in findings if f.truth == FALSE]
    real_ones = [f for f in findings if f.truth == REAL]
    caught = sum(falsified(f) for f in false_ones) / len(false_ones) if false_ones else 1.0
    kept = sum(not falsified(f) for f in real_ones) / len(real_ones) if real_ones else 1.0
    return caught, kept


class DisproveTask:
    """Run the disprove gate over a case's findings and score its verdicts."""

    name = "disprove"
    phase = Phase.DISPROVE

    def run(self, case_dir: Path, opts: RunOptions) -> RunArtifacts:
        manifest = json.loads((case_dir / "manifest.json").read_text())
        findings = parse_findings(manifest)
        repo_dir = create_temp_repo(str(case_dir / "src"), prefix="eval-disprove-")
        log_dir = tempfile.mkdtemp(prefix="eval-disprove-log-")
        session_log = str(Path(log_dir) / "session.jsonl")
        output = Path(log_dir) / "disprove.json"
        # The agent's recipe says the file already exists and is empty.
        output.touch()

        settings = eval.task.resolved_settings(self, opts.model)
        effort = Effort(opts.effort)
        max_turns = agent.phases.phase_turns(Phase.DISPROVE, effort)
        rc = agent.backend.invoke_agent(agent.backend.AgentInvocation(
            prompt=build_prompt(findings, str(output), max_turns),
            cwd=repo_dir,
            session_log=session_log,
            output_path=str(output),
            add_dirs=agent.session.build_add_dirs(repo_dir, log_dir),
            agent=PHASES[Phase.DISPROVE].agent,
            max_turns=max_turns,
            max_budget=agent.phases.phase_budget(Phase.DISPROVE, effort),
            model=settings.model,
            thinking=settings.thinking,
            provider=settings.provider,
            compact_at=settings.compact_at,
            phase=str(Phase.DISPROVE),
            task="eval-disprove",
            repo="eval/corpus",
            rules_home=opts.rules_home,
        ))
        usage = agent.usage.parse_session_log(session_log)
        verdicts = read_verdicts(str(output))
        return RunArtifacts(
            exit_code=rc, usage=usage, outcome=outcome_for(usage),
            temp_dirs=[repo_dir, log_dir],
            data={
                "findings": findings,
                "verdicts": {k: str(v) for k, v in verdicts.items()},
                "summary": f"verdicts: {len(verdicts)}/{len(findings)}"
                + ("" if verdicts else " (output unreadable or empty)"),
                "_outcomes": verdicts,
            },
        )

    def score(self, artifacts: RunArtifacts, manifest: dict) -> ScoringResult:
        caught, kept = score_verdicts(
            parse_findings(manifest), artifacts.data.get("_outcomes", {}),
        )
        usage = artifacts.usage
        return ScoringResult(
            entry_name="", model="", run_index=0,
            outcome=artifacts.outcome,
            recall=caught, precision=kept,
            cost_usd=usage.cost, duration_ms=usage.duration_ms,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            billed_input=usage.billed_input,
            cache_read_ratio=usage.cache_read_ratio,
        )
