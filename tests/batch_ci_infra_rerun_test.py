"""Rate-limited 'Retry preempted jobs' is classified infra, then sent to a person.

Run ``20261015T211317-f108`` (PRs 4089/4116/4195/4200): the CI dashboard named
``Retry preempted jobs → Re-run jobs killed by spot preemption`` with
``gh: API rate limit exceeded for installation``. ``classify_job`` matched
``INFRA_SIGNATURES`` (``rate limit``). ``CIFixAdapter`` skipped every item.
The fix tally listed them under ``skipped``, and ``batch.outcomes.ci_unfixed``
treated any skip as evidence, so the CI step became a ``step_review``.
``gh run rerun --failed`` cleared them. There is no ``run rerun`` anywhere
under ``ai/lib``.

Expected: when every remaining failure is transient infra (GitHub API rate
limit), ``run_fix`` reruns the failed jobs instead of handing them to a
person. Generic INFRA (OOM) still goes to a person.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from conftest import make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.outcomes  # noqa: E402
import batch.steps  # noqa: E402
import fix.ci  # noqa: E402
import fix.engine  # noqa: E402
import gh.client  # noqa: E402
import pr.ci_failures  # noqa: E402
import pr.ci_report  # noqa: E402
import rebase.ci_fix  # noqa: E402
import rebase.inspect  # noqa: E402
from pr.ci_report import CIReport  # noqa: E402


# Headlines from run 20261015T211317-f108, PR 4089 log
# (…/batch/20261015T211317-f108/logs/usemaximum__maximum-4089-ci-0.log).
RATE_LIMIT = (
    "gh: API rate limit exceeded for installation. If you reach out to GitHub "
    "Support for help, please include the request ID"
)
EXIT_ONE = "Process completed with exit code 1."
PREEMPTED_JOB = "Retry preempted jobs"
OOM = "The runner process was killed: oom / out of memory"

ITEM = batch.model.Item(
    key="usemaximum/maximum#4089", repo="usemaximum/maximum", repo_dir=".",
    pr=4089, branch="isaac/ENG-4203/brand_tokens", head_sha="s", worktree="/wt",
)


def _item(item_id, annotation):
    return pr.ci_failures.FailureItem(
        id=item_id, annotation=annotation, file=".github", line=1,
        diagnosis=None, fix_sha=None, outcome=None, headline=annotation,
    )


def _group(*items):
    return pr.ci_failures.FailureGroup(
        job=PREEMPTED_JOB, kind=pr.ci_failures.FailureKind.INFRA, items=items,
        failed_step="Re-run jobs killed by spot preemption",
    )


def _report(failures):
    return CIReport(
        repo="usemaximum/maximum", branch="isaac/ENG-4203/brand_tokens",
        pr_number=4089, run_id=38794327836,
        run_ids=[38794327836, 38794327524, 38794327554],
        run_number=18374, head_sha="93b5acd", conclusion="failure",
        behind_main=0, failures=failures, progression={},
        resolved_since_prior=[],
    )


def _ci_unfixed(result):
    evidence = []
    for decision in result.decisions:
        evidence.extend(decision.payload.get("evidence", []))
    return [e for e in evidence if e.get("kind") == "ci_unfixed"]


def _rerun_argvs(gh_run):
    """Argv lists of ``gh run rerun`` calls on the mocked ``gh.client.run``."""
    found = []
    for call in gh_run.call_args_list:
        argv = [str(a) for a in call.args]
        if "rerun" in argv:
            found.append(argv)
    return found


def _fix_tally(stdout):
    docs = []
    for chunk in stdout.split("---"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            docs.append(json.loads(chunk))
        except json.JSONDecodeError:
            continue
    return next(
        d for d in reversed(docs) if d.get("type") == pr.ci_report.FIX_TALLY_TYPE
    )


def _run_fix_with_rerun(tmp_path, report, *, gh_ok=True, rereport=None):
    """run_fix with gh rerun, wait/re-report, and the engine stubbed."""
    gh_run = MagicMock(return_value=MagicMock(ok=gh_ok))
    engine = MagicMock(return_value=fix.engine.FixRun())
    rereport_fn = MagicMock(
        return_value=_report({}) if rereport is None else rereport,
    )
    with patch.object(fix.engine, "run", engine), \
         patch.object(gh.client, "run", gh_run), \
         patch.object(rebase.ci_fix, "_rereport_after_rerun", rereport_fn), \
         patch.object(rebase.ci_fix, "rebase_if_behind", return_value=False), \
         patch.object(rebase.inspect, "rebase_in_progress", return_value=False):
        rc = rebase.ci_fix.run_fix(
            MagicMock(), report,
            make_ctx(worktree_root=tmp_path, target_dir=tmp_path),
        )
    return rc, engine, gh_run, rereport_fn


def test_rate_limit_on_preempted_jobs_classifies_as_infra():
    """The log line that named the failure is an INFRA_SIGNATURES hit."""
    assert pr.ci_failures.classify_job(
        PREEMPTED_JOB, [RATE_LIMIT],
    ) is pr.ci_failures.FailureKind.INFRA


def test_exit_code_1_alone_on_that_job_is_not_infra():
    """Without a signature, the job name does not match TEST/LINT and is BUILD."""
    assert pr.ci_failures.classify_job(
        PREEMPTED_JOB, [EXIT_ONE],
    ) is pr.ci_failures.FailureKind.BUILD


def test_any_rate_limit_annotation_makes_the_whole_job_infra():
    """One rate-limit line on the job classifies every item in it as infra.

    The dashboard mixed ``Process completed with exit code 1`` (×2) with the
    rate-limit line (×3). classify_job walks every annotation, so the exit-code
    items rode along as infra too — which is how the tally was all skipped.
    """
    assert pr.ci_failures.classify_job(
        PREEMPTED_JOB, [EXIT_ONE, RATE_LIMIT],
    ) is pr.ci_failures.FailureKind.INFRA


def test_adapter_skips_rate_limit_infra_and_hands_the_agent_nothing(tmp_path):
    failures = {
        "retry-preempted-jobs": _group(
            _item("err-.github-14", RATE_LIMIT),
            _item("err-.github-13", EXIT_ONE),
        ),
    }
    adapter = fix.ci.CIFixAdapter(
        _report(failures),
        make_ctx(worktree_root=tmp_path, target_dir=tmp_path),
        MagicMock(),
    )
    assert adapter.fixable == []
    assert [f.id for f in adapter.skipped] == ["err-.github-14", "err-.github-13"]


def test_run_fix_reruns_rate_limit_infra_instead_of_ci_unfixed(tmp_path, capsys):
    """Transient GitHub rate-limit infra is rerun, not handed to a person.

    When every remaining failure is rate-limit infra, ``run_fix`` must
    ``gh run rerun <run_id> --failed`` for each of the report's run ids,
    wait and re-report, and must not classify as ``NEEDS_DECISION`` /
    ``ci_unfixed``. The wait/re-report path is mocked to a green report so
    a rerun-only implementation that still emits the skipped tally cannot
    pass, and an empty-tally shortcut that never waits cannot pass either.
    """
    report = _report({
        "retry-preempted-jobs": _group(_item("err-.github-14", RATE_LIMIT)),
    })
    rc, engine, gh_run, rereport = _run_fix_with_rerun(tmp_path, report)
    engine.assert_not_called()
    rereport.assert_called_once()
    reruns = _rerun_argvs(gh_run)
    assert reruns, "expected gh run rerun --failed for rate-limit infra"
    for run_id in report.run_ids:
        assert any(
            "--failed" in argv and str(run_id) in argv for argv in reruns
        ), f"missing gh run rerun --failed for run {run_id}"

    out = capsys.readouterr().out
    tally = _fix_tally(out)
    assert tally["skipped"] == [] and tally["unfixed"] == [] and tally["fixed"] == []
    result = batch.outcomes.classify(
        batch.model.Step.CI, rc, out, item=ITEM, log_tail=[],
    )
    assert _ci_unfixed(result) == []
    assert result.status is not batch.model.StepStatus.NEEDS_DECISION


def test_run_fix_still_rate_limited_after_rerun_is_ci_unfixed(tmp_path, capsys):
    """One rerun only: still rate-limited after wait stays a person decision."""
    report = _report({
        "retry-preempted-jobs": _group(_item("err-.github-14", RATE_LIMIT)),
    })
    rc, engine, gh_run, rereport = _run_fix_with_rerun(
        tmp_path, report, rereport=report,
    )
    engine.assert_not_called()
    rereport.assert_called_once()
    assert len(_rerun_argvs(gh_run)) == len(report.run_ids)
    assert rc == 0
    result = batch.outcomes.classify(
        batch.model.Step.CI, rc, capsys.readouterr().out, item=ITEM, log_tail=[],
    )
    assert result.status is batch.model.StepStatus.NEEDS_DECISION
    assert _ci_unfixed(result)


def test_run_fix_rerun_gh_failure_is_skipped_tally(tmp_path, capsys):
    """A failed ``gh run rerun`` (including a latched breaker) is today's skip."""
    report = _report({
        "retry-preempted-jobs": _group(_item("err-.github-14", RATE_LIMIT)),
    })
    rc, engine, gh_run, rereport = _run_fix_with_rerun(
        tmp_path, report, gh_ok=False,
    )
    engine.assert_not_called()
    rereport.assert_not_called()
    assert _rerun_argvs(gh_run)
    assert rc == 0
    tally = _fix_tally(capsys.readouterr().out)
    assert tally["skipped"] == [{"id": "err-.github-14", "kind": "infra"}]


def test_run_fix_rerun_then_fixable_failure_enters_the_engine(tmp_path):
    """A real failure after the rerun continues into the fix engine."""
    rate_limited = _report({
        "retry-preempted-jobs": _group(_item("err-.github-14", RATE_LIMIT)),
    })
    fixable = _report({
        "build": pr.ci_failures.FailureGroup(
            job="build", kind=pr.ci_failures.FailureKind.BUILD,
            items=(_item("build-1", "compilation failed"),),
        ),
    })
    rc, engine, gh_run, rereport = _run_fix_with_rerun(
        tmp_path, rate_limited, rereport=fixable,
    )
    engine.assert_called_once()
    rereport.assert_called_once()
    assert len(_rerun_argvs(gh_run)) == len(rate_limited.run_ids)
    assert rc == 0


def test_run_fix_does_not_rerun_oom_infra_and_yields_ci_unfixed(tmp_path, capsys):
    """Generic INFRA (OOM) is not a rerun; it still needs a person."""
    report = _report({
        "retry-preempted-jobs": _group(_item("err-.github-oom", OOM)),
    })
    with patch.object(fix.engine, "run") as engine, \
         patch.object(gh.client, "run") as gh_run:
        rc = rebase.ci_fix.run_fix(
            MagicMock(), report,
            make_ctx(worktree_root=tmp_path, target_dir=tmp_path),
        )
    engine.assert_not_called()
    assert _rerun_argvs(gh_run) == []
    assert rc == 0

    result = batch.outcomes.classify(
        batch.model.Step.CI, rc, capsys.readouterr().out, item=ITEM, log_tail=[],
    )
    assert result.status is batch.model.StepStatus.NEEDS_DECISION
    assert result.decisions[0].kind is batch.model.DecisionKind.STEP_REVIEW
    unfixed = _ci_unfixed(result)
    assert unfixed
    assert unfixed[0]["skipped"] == [{"id": "err-.github-oom", "kind": "infra"}]


def test_batch_ci_step_does_not_ask_ci_check_to_rerun():
    argv = batch.steps.step_argv(
        batch.model.Step.CI, "/bin/pr", "/wt", remote_sha="abc")
    assert argv[0].endswith("ci-check")
    assert "--fix" in argv and "--no-rebase" in argv
    assert "rerun" not in argv
