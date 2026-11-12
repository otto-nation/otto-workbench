"""`pr rebase`'s contract and the vocabulary it renders."""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr_rebase  # noqa: E402
import git.regenerate  # noqa: E402
import pr.domains  # noqa: E402
import rebase.types  # noqa: E402
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
