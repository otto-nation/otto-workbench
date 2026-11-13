"""`pr review`'s contract: prompts then posts; --post is a mode; exit 4 is superseded."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402
import cli.pr_commands  # noqa: E402
import cli.review_entry  # noqa: E402
import pr.supersession  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402


def test_review_help_documents_summary_prompts_and_post_mode():
    text = cli.pr.help_parser("review").format_help()
    assert "REVIEW_SUMMARY:{json}" in text
    assert "prompts" in text
    assert "post an existing review" in text.lower()


def test_review_exit_codes():
    assert [int(o.code) for o in cli.review_entry.CONTRACT.exits] == [
        0, 1, 2, 4, 75, 130]
    assert cli.review_entry.CONTRACT.exits[0].code is ExitCode.OK
    assert cli.review_entry.CONTRACT.exits[3].code is ExitCode.REFUSED


def test_the_supersession_override_is_force_superseded():
    assert pr.supersession.OVERRIDE_FLAG == "--force-superseded"
    parser = cli.review_entry.build_parser()
    assert parser.parse_args(["--force-superseded"]).force_superseded is True
    assert parser.parse_args(["--force"]).force is True
    assert parser.parse_args(["--force"]).force_superseded is False
    grouped = [k for g in cli.review_entry.CONTRACT.groups for k in g.flags]
    assert "--force-superseded" in grouped
    grouped_fix = [k for g in cli.pr_commands.FIX_CONTRACT.groups for k in g.flags]
    assert "--force-superseded" in grouped_fix


def test_self_fix_push_effect_mentions_the_fix_commit_push():
    effects = {e.trigger: e for e in cli.review_entry.CONTRACT.remote_effects}
    effect = effects["--self --fix --push"]
    text = " ".join((effect.writes, effect.how, effect.on_refusal))
    assert effect.how.endswith("never forced.")
    assert "plain, never forced" in text
    assert "commit stays local" in effect.on_refusal or "commit local" in effect.on_refusal
