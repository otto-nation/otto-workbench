"""The verify eval task: hand the gate claimed fixes, score its verdicts.

The verify gate decides which fixes are published to a reviewer as done, so its
two errors cost differently. Calling a broken fix `verified` ships a defect under
a commit citing it; calling a good fix `broken` or `not verified` only costs a
re-run. A case therefore holds a small repo (`src/`, committed), the edits a fix
pass made to it (`fix/`, left uncommitted in the worktree as the gate finds
them), and a list of claimed fixes each marked `holds` (the edit does what the
reviewer asked) or `broken` (it does not, however sound the claim reads).

| Score field | Meaning here |
|---|---|
| `recall` | Share of the `broken` fixes the gate did not call `verified` — the bad fixes it stopped |
| `precision` | Share of the `holds` fixes the gate called `verified` — 1.0 means it cleared every good one |

A fix with no verdict is unverified, as the engine reads it, so a gate that wrote
nothing scores recall 1.0 and precision 0.0: it let nothing bad through and
cleared nothing. A case with no fix of one kind scores that field 1.0.

Each fix reaches the gate as production builds it: `fix.gate._verify_item`
composes the reviewer's ask with the pass's claim and what the worktree shows,
and the tracking file and prompt come from the production template. What the
eval does not exercise is the review pipeline's chunking around the gate.
"""

# doc-group: eval

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import agent.backend
import agent.phases
import agent.session
import agent.templates
import agent.usage
import fix.gate
import fix.scope
import fix.tracking
from agent.registry import PHASES
from core.phases import Effort, Phase
from eval.scoring import ScoringResult
import eval.task
from eval.task import RunArtifacts, RunOptions, create_temp_repo, outcome_for
from fix.types import FixItem
from pr.fix import FixOutcome, ItemOutcome

HOLDS = "holds"
BROKEN = "broken"
_TRUTHS = (HOLDS, BROKEN)

# What the gate calls one unit of its work, in the prompt's own words.
_NOUN = "fix"


@dataclass(frozen=True)
class CaseFix:
    """One claimed fix: what was asked, what the pass said, and whether it holds."""

    id: str
    truth: str
    file: str
    line: int
    ask: str
    claim: str


def parse_fixes(manifest: dict) -> list[CaseFix]:
    """The manifest's claimed fixes. Raises `ValueError` for one a run could not score."""
    out = []
    for raw in manifest.get("items", []):
        item = CaseFix(
            id=raw["id"], truth=raw["truth"], file=raw["file"], line=raw["line"],
            ask=raw["ask"], claim=raw["claim"],
        )
        if item.truth not in _TRUTHS:
            raise ValueError(f"{item.id}: truth must be one of {_TRUTHS}, got {item.truth!r}")
        out.append(item)
    if len({i.id for i in out}) != len(out):
        raise ValueError("item ids must be unique")
    if not out:
        raise ValueError("a verify case needs at least one item")
    return out


def overlay_files(case_dir: Path) -> list[str]:
    """Repo-relative paths of the edits the case's fix pass made, sorted."""
    fix_dir = case_dir / "fix"
    return sorted(
        str(p.relative_to(fix_dir)) for p in fix_dir.rglob("*") if p.is_file()
    )


def build_items(fixes: list[CaseFix], changed: list[str]) -> list[FixItem]:
    """The claimed fixes as the gate is asked about them in production."""
    scope = fix.scope.BatchScope(files=frozenset(changed))
    items = []
    for f in fixes:
        outcome = ItemOutcome(
            id=f.id, outcome=FixOutcome.FIXED, summary=f.ask.splitlines()[0],
            reason=f.claim, file=f.file, line=f.line,
        )
        source = FixItem(id=f.id, file=f.file, line=f.line, label="reviewer", body=f.ask)
        items.append(fix.gate._verify_item(outcome, source, _NOUN, scope))
    return items


def build_prompt(
    items: list[FixItem], tracking_path: Path, repo_dir: str, max_turns: int,
) -> str:
    """The production verify prompt over *items*; writes the tracking file it names."""
    fix.tracking.write(tracking_path, "Verify Fixes", items, fix.tracking.VERIFY_BOXES)
    return agent.templates.render(
        PHASES[Phase.FIX_VERIFY].template_for(),
        branch_name="eval",
        repo="eval/corpus",
        tracking_content=tracking_path.read_text(),
        tracking_file=str(tracking_path),
        answer_format=fix.tracking.verify_instructions(_NOUN),
        execution_claim_guard=agent.templates.build_execution_claim_guard(
            occasion=agent.templates.ClaimOccasion.FIX_EVIDENCE,
        ),
        worktree_block=agent.templates.build_worktree_block(repo_dir),
        max_turns=str(max_turns),
    )


def read_verdicts(tracking_path: Path) -> dict[str, bool | None]:
    """Item id -> `True` verified, `False` broken, `None` not verified. Unanswered is absent."""
    return {
        item_id: ok
        for item_id, (ok, _detail) in fix.tracking.parse_verdicts(tracking_path).items()
    }


def score_verdicts(
    fixes: list[CaseFix], verdicts: dict[str, bool | None],
) -> tuple[float, float]:
    """`(share of broken fixes not called verified, share of good fixes called verified)`."""
    broken = [f for f in fixes if f.truth == BROKEN]
    good = [f for f in fixes if f.truth == HOLDS]
    stopped = sum(verdicts.get(f.id) is not True for f in broken) / len(broken) if broken else 1.0
    cleared = sum(verdicts.get(f.id) is True for f in good) / len(good) if good else 1.0
    return stopped, cleared


class VerifyTask:
    """Run the verify gate over a case's claimed fixes and score its verdicts."""

    name = "verify"
    phase = Phase.FIX_VERIFY

    def run(self, case_dir: Path, opts: RunOptions) -> RunArtifacts:
        manifest = json.loads((case_dir / "manifest.json").read_text())
        fixes = parse_fixes(manifest)
        repo_dir = create_temp_repo(str(case_dir / "src"), prefix="eval-verify-")
        # The fix pass's edits stay uncommitted: the gate is told `git diff` is
        # exactly what the pass changed.
        shutil.copytree(case_dir / "fix", repo_dir, dirs_exist_ok=True)
        log_dir = tempfile.mkdtemp(prefix="eval-verify-log-")
        session_log = str(Path(log_dir) / "session.jsonl")
        tracking = Path(log_dir) / "verify.md"

        settings = eval.task.resolved_settings(self, opts.model)
        effort = Effort(opts.effort)
        max_turns = agent.phases.phase_turns(Phase.FIX_VERIFY, effort, items=len(fixes))
        prompt = build_prompt(
            build_items(fixes, overlay_files(case_dir)), tracking, repo_dir, max_turns,
        )
        rc = agent.backend.invoke_fix(agent.backend.AgentInvocation(
            prompt=prompt,
            cwd=repo_dir,
            session_log=session_log,
            add_dirs=agent.session.build_add_dirs(repo_dir, log_dir),
            max_turns=max_turns,
            max_budget=agent.phases.phase_budget(Phase.FIX_VERIFY, effort, items=len(fixes)),
            model=settings.model,
            thinking=settings.thinking,
            provider=settings.provider,
            compact_at=settings.compact_at,
            phase=str(Phase.FIX_VERIFY),
            task="eval-verify",
            repo="eval/corpus",
            rules_home=opts.rules_home,
        ))
        usage = agent.usage.parse_session_log(session_log)
        verdicts = read_verdicts(tracking)
        return RunArtifacts(
            exit_code=rc, usage=usage, outcome=outcome_for(usage),
            temp_dirs=[repo_dir, log_dir],
            data={
                "fixes": fixes,
                "verdicts": {k: str(v) for k, v in verdicts.items()},
                "summary": f"verdicts: {len(verdicts)}/{len(fixes)}"
                + ("" if verdicts else " (no box ticked)"),
                "_verdicts": verdicts,
            },
        )

    def score(self, artifacts: RunArtifacts, manifest: dict) -> ScoringResult:
        stopped, cleared = score_verdicts(
            parse_fixes(manifest), artifacts.data.get("_verdicts", {}),
        )
        usage = artifacts.usage
        return ScoringResult(
            entry_name="", model="", run_index=0,
            outcome=artifacts.outcome,
            recall=stopped, precision=cleared,
            cost_usd=usage.cost, duration_ms=usage.duration_ms,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            billed_input=usage.billed_input,
            cache_read_ratio=usage.cache_read_ratio,
        )
