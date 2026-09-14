"""The triage prompt's exact text, locked.

The prompt is a behavioural contract with a model, not an internal string:
the vocabulary it names and the schema it shows are what comes back. A
refactor that renders the same values from an enum must not change a byte of
it, and only a golden comparison can show that.

Nothing else in the suite compares the *whole* rendered prompt. Every other
assertion over `triage_prompt.build_triage_prompt` is a substring check on a
value somebody thought to name — a verification definition, a schema key, a
commit-log sentence — so a change to a part nobody named passes all of them.
Whitespace in a re-rendered schema line, a reworded complexity example, a
dropped field in the `comment_items` block: none of those trip a named
substring. The golden is what would.

Two fixtures, not one. The `comment_items` schema block is only spliced in
when `unseen_comments` is non-empty, and that block is one of the two copies
a later collapse must prove identical. A single fixture would leave the other
copy untested.

The fixtures are byte-exact renderer output and are written, never
hand-edited. An editor set to trim trailing whitespace on save will silently
eat a trailing blank line and corrupt the comparison.

Regenerate the fixtures by calling `_write_goldens()` from a throwaway test
in this directory — `conftest.py` is the one place allowed to load a script
from a path, and a bare `python3 -c` outside pytest will not resolve the
imports. Prose rather than a flag for the same reason
`summary_body_golden_test.py` uses prose: one regeneration idiom in the repo
is better than two.

A diff in one of these files is a change to what the model is asked and is
read as one. During the decomposition this prompt is being split for, the
golden must be **re-run and re-asserted, never regenerated**: regenerating it
records whatever the split produced and asserts nothing about it.
"""

import sys
from pathlib import Path

from conftest import REPO_ROOT

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from pr.comments_state import ThreadState  # noqa: E402
from pr.thread_models import ReportThread  # noqa: E402
from pr import triage_prompt  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Realistic enough that json.dumps of asdict(thread) is a non-empty block, and
# that the comment_items schema is actually spliced. build_triage_prompt takes
# list[ReportThread], not dicts — the dict shape in the task brief would raise.
THREADS = [
    ReportThread(
        id="t1",
        file="ai/lib/pr/triage.py",
        line=42,
        reviewer="alice",
        state=ThreadState.NEW,
        comments=[{"user": "alice", "body": "rename this flag"}],
    ),
]

COMMENTS = [
    {
        "id": "c1",
        "body": "two things: rename the flag, and drop the dead branch",
        "user": "kgn",
        "source_type": "issue_comment",
    },
]

DIFF = "diff --git a/x.py b/x.py\n+pass\n"


def _render_both():
    """Both shapes the template renders: with and without the items block."""
    return {
        "triage_prompt_threads_only.txt": triage_prompt.build_triage_prompt(
            THREADS, DIFF,
        ),
        "triage_prompt_with_comments.txt": triage_prompt.build_triage_prompt(
            THREADS, DIFF, unseen_comments=COMMENTS,
        ),
    }


def _write_goldens():
    """Regenerate the fixtures. Called by hand, never by the suite.

    Prose rather than a flag, matching `summary_body_golden_test.py`: one
    regeneration idiom in the repo is better than two.
    """
    for name, text in _render_both().items():
        (FIXTURES / name).write_text(text)


@pytest.mark.parametrize("name", [
    "triage_prompt_threads_only.txt",
    "triage_prompt_with_comments.txt",
])
def test_the_prompt_text_is_unchanged(name):
    expected = (FIXTURES / name).read_text()
    assert _render_both()[name] == expected
