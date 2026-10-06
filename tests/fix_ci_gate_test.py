"""Tests for the CI fix pass's verify gate — the phase it is asked under, where
its checklists land, and what a verdict does to a claimed CI fix.
"""

import sys
from pathlib import Path

from conftest import make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import fix.ci  # noqa: E402
import fix.gate  # noqa: E402
import pr.ci_failures  # noqa: E402
from agent.registry import PHASES, RETRYABLE_FIX_PHASES  # noqa: E402
from core.phases import Phase  # noqa: E402
from pr.ci_report import CIReport  # noqa: E402
from pr.fix import FixOutcome  # noqa: E402
from pr.state import PRIdentity, PRState  # noqa: E402

from fix_engine_support import _answer, _run, _verdicts, head, landed  # noqa: F401
# autouse: stubs the worktree snapshots every pass reads; imported so it applies here
from fix_engine_support import snapshots  # noqa: F401


def _adapter(tmp_path):
    """The CI adapter as `rebase.ci_fix.run_fix` builds it, with one build failure."""
    item = pr.ci_failures.FailureItem(
        id="b-1", annotation="boom", file="src/a.py", line=3, diagnosis=None,
        fix_sha=None, outcome=None, headline="boom",
    )
    group = pr.ci_failures.FailureGroup(
        job="build", kind=pr.ci_failures.FailureKind.BUILD, items=(item,),
    )
    report = CIReport(
        repo="owner/repo", branch="feat/test", pr_number=42,
        run_id=100, run_ids=[100], run_number=7, head_sha="abc123",
        conclusion="failure", behind_main=0, failures={"build": group},
        progression={}, resolved_since_prior=[],
    )
    state = PRState(identity=PRIdentity(
        repo="owner/repo", branch="feat/test", pr_number=42,
        head_sha="abc123", worktree_root="",
    ))
    return fix.ci.CIFixAdapter(
        report, make_ctx(worktree_root=tmp_path, target_dir=tmp_path), state,
    )


def test_the_ci_adapter_declares_the_gate_s_own_phase(tmp_path):
    """Without it the gate is sized as the fix pass and handed `fix-ci.md`.

    That template tells its agent to edit source, which `verify-fixes.md`
    forbids; the fallback to `phase` is for a domain that runs no gate.
    """
    adapter = _adapter(tmp_path)

    assert adapter.verify_phase is Phase.CI_VERIFY
    assert PHASES[adapter.verify_phase].template_for() == "verify-fixes.md"
    assert adapter.verify_phase not in RETRYABLE_FIX_PHASES


def test_the_gate_s_checklists_land_beside_the_fix_pass_s_own(tmp_path):
    """Under the run's target directory, not the worktree, chunk-indexed."""
    adapter = _adapter(tmp_path)
    artifacts = tmp_path / "ci-failures"

    assert adapter.verify_tracking_path(1) == artifacts / "verify-tracking-1.md"
    assert adapter.verify_session_log(2) == artifacts / "verify-session-2.jsonl"


def test_a_ci_fix_the_gate_finds_broken_is_a_person_s_call(tmp_path, landed, head):
    """A ticked box over a check that still fails is not a fix.

    Through the engine, so what is asserted is what the pass records: the gate
    is asked under `ci_verify`, and its `broken` demotes the claim before the
    commit is assembled.
    """
    adapter = _adapter(tmp_path)
    seen = {}
    broken = _verdicts(("b-1", fix.gate.Verdict(ok=False, detail="build still fails")))

    def run_verify(phase, prompt, **kwargs):
        seen["phase"] = phase
        return broken(phase, prompt, **kwargs)

    run, _ = _run(adapter, verify=run_verify)

    assert seen["phase"] is Phase.CI_VERIFY
    assert run.outcomes[0].outcome is FixOutcome.NEEDS_HUMAN
    assert "build still fails" in run.outcomes[0].reason
    assert adapter.state.ci.fix.items[0].outcome is FixOutcome.NEEDS_HUMAN


def test_a_ci_fix_the_gate_confirms_stays_fixed(tmp_path, landed, head):
    """The negative control: a verified claim survives the gate unchanged."""
    adapter = _adapter(tmp_path)

    run, _ = _run(
        adapter,
        verify=_verdicts(("b-1", fix.gate.Verdict(ok=True, detail="build green"))),
    )

    assert run.outcomes[0].outcome is FixOutcome.FIXED
    assert run.outcomes[0].verified is True
