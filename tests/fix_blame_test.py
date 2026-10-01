"""Tests for fix.blame — which item a red suite is probably about.

The two filters this module turns on were measured, not guessed: plain token
intersection implicated 11% of 319 changed files against a failure none of
them caused. So the cases that matter here are the negative ones — prose, a
common name, a symbol the change put back — because those are what separate a
pointer worth reading from a plausible-looking guess.

Driven against a real git worktree rather than a stubbed diff: the subject is
"what did this change remove", and a hand-written diff string would be the
test agreeing with itself about what git produces.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import fix.blame  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    """A committed worktree the tests then edit, so `git diff HEAD` has work."""
    def git(*args):
        subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True,
            capture_output=True, text=True,
        )
    git("init", "--quiet")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    return tmp_path


def _commit(repo, name, text):
    (repo / name).write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "-A"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"],
                   check=True, capture_output=True)


# ── what counts as a loss ───────────────────────────────────────────────────


def test_a_deleted_symbol_the_failure_names_points_at_its_item(repo):
    """The motivating case: an import removed, and a test that read it off.

    This is #1559's first regression in miniature — the symbol is gone from
    the file and the AttributeError names it.
    """
    _commit(repo, "mod.py", "from x import EXIT_BUDGET_EXHAUSTED, cmd_gc\n")
    (repo / "mod.py").write_text("from x import cmd_gc\n")
    failure = "AttributeError: module 'mod' has no attribute 'EXIT_BUDGET_EXHAUSTED'"

    found = fix.blame.pointers(repo, {"N1": "mod.py"}, failure)

    assert [p.item_id for p in found] == ["N1"]
    assert found[0].symbols == ("EXIT_BUDGET_EXHAUSTED",)


def test_a_renamed_symbol_points_at_the_old_name(repo):
    """#1559's second regression: the message moved on, the assertion did not."""
    _commit(repo, "p.py", 'MSG = "teach _positional_index in ai/bin/pr"\n')
    (repo / "p.py").write_text('MSG = "teach positional_index in cli.dispatch"\n')
    failure = 'assert "_positional_index" in str(exc.value)'

    found = fix.blame.pointers(repo, {"N2": "p.py"}, failure)

    assert found[0].symbols == ("_positional_index",)


def test_the_new_name_is_not_reported_as_lost(repo):
    """A rename removes one name and adds another; only one of them is gone."""
    _commit(repo, "p.py", 'MSG = "see _old_helper here"\n')
    (repo / "p.py").write_text('MSG = "see new_helper_name here"\n')

    found = fix.blame.pointers(repo, {"N1": "p.py"}, "new_helper_name missing")

    assert found == ()


def test_a_name_still_used_elsewhere_in_the_file_is_not_lost(repo):
    """Removing one of several references is not a deletion of the symbol."""
    _commit(repo, "m.py", "CALL_ME()\nCALL_ME()\n")
    (repo / "m.py").write_text("CALL_ME()\n")

    assert fix.blame.pointers(repo, {"N1": "m.py"}, "CALL_ME exploded") == ()


def test_a_removed_line_that_itself_starts_with_dashes_is_still_read(repo):
    """A `--` comment line arrives in the diff as `---` and looked like a header.

    Counting dashes cannot tell a removed `-- DROP_ME` from the `--- a/path`
    header, so the symbol was dropped and the item went unattributed. The
    parse keys off the `@@` boundary instead, where the distinction is exact.
    """
    _commit(repo, "q.sql", "-- uses DROP_ME_SENTINEL here\nSELECT 1;\n")
    (repo / "q.sql").write_text("SELECT 1;\n")

    found = fix.blame.pointers(repo, {"N1": "q.sql"}, "DROP_ME_SENTINEL missing")

    assert found and found[0].symbols == ("DROP_ME_SENTINEL",)


def test_a_removed_markdown_rule_line_is_still_read(repo):
    """The four-dash case: a removed `--- X` becomes `---- X` in the diff."""
    _commit(repo, "d.md", "--- RULE_SENTINEL ---\ntext\n")
    (repo / "d.md").write_text("text\n")

    found = fix.blame.pointers(repo, {"N1": "d.md"}, "RULE_SENTINEL gone")

    assert found and found[0].symbols == ("RULE_SENTINEL",)


def test_the_file_header_itself_is_never_read_as_content(repo):
    """The reason the dash test existed: `a/path` must not become a symbol."""
    _commit(repo, "My_Module.py", "X = 1\n")
    (repo / "My_Module.py").write_text("Y = 2\n")

    # The header line is `--- a/My_Module.py`; if it were read as removed
    # content, `My_Module` would be offered as a lost symbol.
    found = fix.blame.pointers(repo, {"N1": "My_Module.py"}, "My_Module blew up")

    assert found == ()


# ── the filters that were measured, not guessed ─────────────────────────────


def test_prose_removed_from_a_comment_points_at_nothing(repo):
    """The filter that took the false-positive rate from 11% to 0.9%.

    A diff carries comments and markdown, a test runner's output is full of
    English, and matching `review` or `gate` against a failure that merely
    discusses them is how a confident wrong pointer gets built.
    """
    _commit(repo, "m.py", "# the review gate could fail differently here\nX = 1\n")
    (repo / "m.py").write_text("X = 1\n")
    failure = "the review gate could fail differently and that is the problem"

    assert fix.blame.pointers(repo, {"N1": "m.py"}, failure) == ()


def test_a_name_common_across_the_repo_points_at_nothing(repo):
    """`read_text` and `Path` were the entire remaining tail at 0.9%.

    A symbol a dozen files mention is vocabulary, not a fingerprint: seeing it
    in a traceback says nothing about who removed one use of it.
    """
    for n in range(_over_the_rarity_cap()):
        _commit(repo, f"other{n}.py", "value = thing.read_text()\n")
    _commit(repo, "m.py", "data = path.read_text()\n")
    (repo / "m.py").write_text("data = None\n")

    found = fix.blame.pointers(repo, {"N1": "m.py"}, "read_text blew up")

    assert found == (), "a repo-wide name was treated as a fingerprint"


def _over_the_rarity_cap() -> int:
    """Enough sibling files to push a symbol past `_MAX_FILES`."""
    return fix.blame._MAX_FILES + 2


def test_a_rare_name_is_still_reported_when_the_repo_is_large(repo):
    """The rarity filter must not swallow the signal it was added beside."""
    for n in range(_over_the_rarity_cap()):
        _commit(repo, f"other{n}.py", "value = thing.read_text()\n")
    _commit(repo, "m.py", "X = VERY_RARE_SENTINEL\n")
    (repo / "m.py").write_text("X = None\n")

    found = fix.blame.pointers(repo, {"N1": "m.py"}, "VERY_RARE_SENTINEL missing")

    assert found[0].symbols == ("VERY_RARE_SENTINEL",)


# ── what it refuses to answer ───────────────────────────────────────────────


def test_an_item_whose_file_the_pass_never_touched_is_not_pointed_at(repo):
    _commit(repo, "a.py", "LOST_SYMBOL = 1\n")
    _commit(repo, "b.py", "OTHER = 2\n")
    (repo / "a.py").write_text("")

    found = fix.blame.pointers(repo, {"N1": "a.py", "N2": "b.py"}, "LOST_SYMBOL")

    assert [p.item_id for p in found] == ["N1"]


def test_an_empty_failure_text_points_at_nothing(repo):
    """A green run has no text, and a pointer off no evidence is a guess."""
    _commit(repo, "m.py", "LOST_SYMBOL = 1\n")
    (repo / "m.py").write_text("")

    assert fix.blame.pointers(repo, {"N1": "m.py"}, "   ") == ()


def test_a_missing_anchor_file_is_skipped_rather_than_raising(repo):
    """An item can name a path that was never tracked; that is not a crash."""
    _commit(repo, "m.py", "X = 1\n")

    assert fix.blame.pointers(repo, {"N1": "gone.py", "N2": ""}, "anything") == ()


def test_a_wholesale_file_deletion_still_points_at_its_item(repo):
    """The fix can delete the whole file rather than edit it.

    That is exactly the shape of the motivating regression if the deleted
    symbol lived in a module the pass removed outright: the anchor file no
    longer exists, but the diff still shows what it used to contain. A
    missing post-image must read as empty, not as a reason to skip.
    """
    _commit(repo, "mod.py", "from x import EXIT_BUDGET_EXHAUSTED, cmd_gc\n")
    (repo / "mod.py").unlink()
    failure = "AttributeError: module 'mod' has no attribute 'EXIT_BUDGET_EXHAUSTED'"

    found = fix.blame.pointers(repo, {"N1": "mod.py"}, failure)

    assert [p.item_id for p in found] == ["N1"]
    assert found[0].symbols == ("EXIT_BUDGET_EXHAUSTED",)


def test_two_items_in_one_file_both_point_at_it(repo):
    """The documented ceiling, asserted so it is a choice and not a surprise.

    Attribution is per anchor file. Two findings in one file are
    indistinguishable, and the honest output is both of them rather than a
    coin flip presented as a lead.
    """
    _commit(repo, "m.py", "FIRST_GONE = 1\nSECOND_GONE = 2\n")
    (repo / "m.py").write_text("")

    found = fix.blame.pointers(
        repo, {"N1": "m.py", "N2": "m.py"}, "FIRST_GONE and SECOND_GONE")

    assert [p.item_id for p in found] == ["N1", "N2"]


# ── how it reads ────────────────────────────────────────────────────────────


def test_the_description_leads_rather_than_accuses():
    """It says where to start. It does not say the item is wrong."""
    lines = fix.blame.describe((
        fix.blame.Pointer("N1", "ai/lib/cli/pr.py", ("EXIT_BUDGET_EXHAUSTED",)),
    ))

    assert "start here" in lines[0]
    assert "[N1] ai/lib/cli/pr.py" in lines[1]
    assert "EXIT_BUDGET_EXHAUSTED" in lines[1]
    assert not any("wrong" in line or "broke" in line for line in lines)


def test_nothing_to_point_at_prints_nothing():
    assert fix.blame.describe(()) == []
