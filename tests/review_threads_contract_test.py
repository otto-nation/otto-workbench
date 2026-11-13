"""`pr comments`' contract: --post publishes threads; --finish --post pushes a held commit."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402
import cli.review_threads  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402


def test_comments_help_names_the_command_as_typed():
    assert cli.pr.help_parser("comments").prog == "pr comments"


def test_triage_help_says_resolutions_need_post():
    assert "needs --post" in cli.pr.help_parser("comments").format_help()


def test_post_effect_lists_replies_resolutions_and_summary():
    effects = {e.trigger: e for e in cli.review_threads.CONTRACT.remote_effects}
    text = " ".join((effects["--post"].writes, effects["--post"].how))
    assert "replies" in text and "resolutions" in text and "summary" in text


def test_finish_post_mentions_held_commit_push_and_no_push_verify():
    effects = {e.trigger: e for e in cli.review_threads.CONTRACT.remote_effects}
    effect = effects["--finish --post"]
    text = " ".join((effect.writes, effect.how, effect.on_refusal))
    assert "held" in text.lower() and "commit" in text
    assert "--no-push-verify" in text


def test_comments_exit_codes():
    assert [int(o.code) for o in cli.review_threads.CONTRACT.exits] == [
        0, 1, 2, 75]
    assert cli.review_threads.CONTRACT.exits[0].code is ExitCode.OK
