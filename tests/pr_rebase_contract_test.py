"""`pr rebase`'s contract and the vocabulary it renders."""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr_rebase  # noqa: E402
import cli.pr_push  # noqa: E402
import core.command_contract  # noqa: E402
import git.regenerate  # noqa: E402
import pr.domains  # noqa: E402
import rebase.lease  # noqa: E402
import rebase.types  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402
from rebase.types import ConflictStrategy, RefusalSignal  # noqa: E402


def test_every_strategy_says_what_it_does_with_and_without_fix():
    for s in ConflictStrategy:
        assert s.doc.what and s.doc.without_fix and s.doc.with_fix, s


def test_the_regenerate_doc_names_the_lockfiles_from_their_owner():
    for name in git.regenerate.LOCKFILE_REGENERATORS:
        assert name in ConflictStrategy.REGENERATE.doc.what


def test_only_generated_classes_continue_without_fix():
    assert "continues" in ConflictStrategy.REGENERATE.doc.without_fix
    assert "continues" in ConflictStrategy.ACCEPT_THEIRS.doc.without_fix
    for s in (ConflictStrategy.DELETE, ConflictStrategy.BINARY_ERROR, ConflictStrategy.AI_MERGE):
        assert "aborts" in s.doc.without_fix, s


def test_every_refusal_signal_is_described():
    for s in RefusalSignal:
        assert s.doc.status and s.doc.found, s


def test_the_landed_override_is_force_landed_everywhere():
    assert pr.domains.REBASE_LANDED_OVERRIDE == "--force-landed"
    assert rebase.types.REFUSAL_OVERRIDE_FLAG == "--force-landed"
    assert rebase.types.RefusalReport("b", "pr_merged", "d").override == "--force-landed"


def test_force_landed_and_the_hidden_force_alias_set_the_same_flag():
    parser = cli.pr_rebase.build_parser()
    assert parser.parse_args(["--force-landed"]).force is True
    assert parser.parse_args(["--force"]).force is True
    hidden = [a for a in parser._actions if "--force" in a.option_strings]
    assert hidden and hidden[0].help is argparse.SUPPRESS


def test_the_status_line_names_the_new_override():
    summary = pr.domains.RebaseSummary(status="already_landed", updated_at="x")
    assert "pr rebase --force-landed" in " ".join(summary.render_status())


def _effect(contract, trigger):
    return next(e for e in contract.remote_effects if e.trigger == trigger)


def test_the_lease_argv_in_the_contract_is_the_one_pushed():
    """The contract quotes the refspec PushLease builds, so the two cannot drift."""
    argv = rebase.lease.PushLease(branch="<branch>", expect="<expect>").args[0]
    assert argv in _effect(cli.pr_rebase.CONTRACT, "").how
    assert argv in _effect(cli.pr_rebase.CONTRACT, "--push-only --expect").how


def test_push_only_leases_on_the_recorded_rebase():
    how = _effect(cli.pr_rebase.CONTRACT, "--push-only").how
    assert "lease_expect" in how and "state.json" in how


def test_no_verify_skips_the_hook_but_not_the_owner_check():
    text = _effect(cli.pr_rebase.CONTRACT, "--no-verify").how
    assert "--no-verify" in text and "owner check" in text


def test_rebase_exit_codes():
    codes = [int(o.code) for o in cli.pr_rebase.CONTRACT.exits]
    assert codes == [0, 1, 2, 3, 4, 75, 130]


def test_the_classes_vocabulary_is_rendered_from_the_enum():
    enums = {d.field: d for d in cli.pr_rebase.CONTRACT.json_output.enums}
    values = {v.value: v.meaning for v in enums["classes"].values}
    assert set(values) == {s.value for s in ConflictStrategy}
    assert ConflictStrategy.DELETE.doc.with_fix in values["delete"]


def test_push_never_creates_a_branch_and_says_so():
    assert "never creates" in _effect(cli.pr_push.CONTRACT, "").on_refusal


def test_push_exit_codes():
    assert [int(o.code) for o in cli.pr_push.CONTRACT.exits] == [0, 1, 2, 75]


def _contract_json_names(cls: type) -> list[str]:
    report = next(r for r in cli.pr_rebase.CONTRACT.json_output.reports if r.cls is cls)
    return list(core.command_contract._json_field_names(report.cls))


def _emit_keys(report, capsys) -> list[str]:
    report.emit()
    return list(json.loads(capsys.readouterr().out))


def test_rebase_outcome_contract_names_match_emit(capsys):
    outcome = rebase.types.RebaseOutcome(force_pushed=True, target_base="origin/main")
    assert _contract_json_names(rebase.types.RebaseOutcome) == _emit_keys(outcome, capsys)


def test_conflict_report_contract_names_match_emit(capsys):
    report = rebase.types.ConflictReport(
        status="conflicts", files=[], rebase_head="",
        rebase_head_subject="", remaining_commits=0,
    )
    assert _contract_json_names(rebase.types.ConflictReport) == _emit_keys(report, capsys)


def test_refusal_report_contract_names_match_emit(capsys):
    report = rebase.types.RefusalReport("b", "pr_merged", "d")
    assert _contract_json_names(rebase.types.RefusalReport) == _emit_keys(report, capsys)
