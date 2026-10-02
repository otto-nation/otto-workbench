"""fix.tracking: comment tracking survives a round trip through state."""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _fix_adapter, _no_published_summary  # noqa: E402
import fix.tracking
import git.topology
import pr.thread_context
from pr.fix import FixOutcome
from pr.thread_models import CommentItem, PRReport, TrackingResult


class TestCommentTrackingRoundTrip:
    """What the agent records comes back on the thread that earned it.

    The file format is `fix.tracking`'s and is tested there. What is tested here
    is the domain's half of the round trip: the section bodies this pass renders,
    and the entries the parsed verdicts are attached back onto.
    """

    def _thread(self, tid="t1"):
        return CommentItem(id=tid, file="a.py", line=3, reviewer="kgn",
                           summary="rename it")

    def _built(self, tmp_path, threads, comment_items=()):
        """Write the checklist the way `fix.engine` writes it for this adapter."""
        adapter = _fix_adapter(
            tmp_path,
            report=PRReport(repo="owner/repo", pr_number=42),
            fixable=list(threads), fixable_items=list(comment_items),
        )
        with patch.object(pr.thread_context, "diff_context_for_file", return_value=""), \
             patch.object(git.topology, "default_branch_cached", return_value="main"):
            fix.tracking.write(
                adapter.tracking_path, adapter.title, adapter.items(),
            )
        return adapter.tracking_path

    def _answer(self, path, label, reason=""):
        """Tick one box the way the agent's Edit would."""
        suffix = f" — {reason}" if reason else ""
        placeholder = "" if label == "fixed" else " — <why>"
        path.write_text(path.read_text().replace(
            f"- [ ] {label}{placeholder}", f"- [x] {label}{suffix}", 1,
        ))

    def _parsed(self, path, threads, comment_items=()):
        return TrackingResult.from_outcomes(
            fix.tracking.parse(path), list(threads),
            fixable_items=list(comment_items),
        )

    def test_the_section_carries_the_id_the_reviewer_and_the_context(self, tmp_path):
        text = self._built(tmp_path, [self._thread()]).read_text()
        assert text.startswith("# Comment Fix Tracking — PR #42\n")
        assert "## <!-- fix:t1 --> a.py:3 — @kgn" in text
        assert "**Summary:** rename it" in text

    def test_a_ticked_fix_comes_back_as_the_entry_the_pass_handed_over(self, tmp_path):
        threads = [self._thread()]
        path = self._built(tmp_path, threads)
        self._answer(path, "fixed")
        result = self._parsed(path, threads)
        assert [e.id for e in result.bucket(FixOutcome.FIXED)] == ["t1"]
        assert result.bucket(FixOutcome.FIXED)[0].reviewer == "kgn"

    def test_a_declined_thread_keeps_the_agent_s_own_words(self, tmp_path):
        threads = [self._thread()]
        path = self._built(tmp_path, threads)
        self._answer(path, "declined", "the helper it names does not exist")
        entry = self._parsed(path, threads).bucket(FixOutcome.DECLINED)[0]
        assert entry.reason == "the helper it names does not exist"

    def test_a_verdict_with_no_reason_still_says_something(self, tmp_path):
        threads = [self._thread()]
        path = self._built(tmp_path, threads)
        self._answer(path, "needs a person")
        entry = self._parsed(path, threads).bucket(FixOutcome.NEEDS_HUMAN)[0]
        assert entry.reason == "agent could not auto-fix"

    def test_an_untouched_thread_is_work_still_owed(self, tmp_path):
        threads = [self._thread()]
        path = self._built(tmp_path, threads)
        entry = self._parsed(path, threads).bucket(FixOutcome.DEFERRED)[0]
        assert entry.reason == "agent could not auto-fix"

    def test_a_comment_item_is_kept_apart_from_a_thread(self, tmp_path):
        """Only a thread has somewhere to reply, so the two never merge."""
        items = [CommentItem(id="c9", file="b.py", line=1, reviewer="ana",
                             body="two spaces")]
        path = self._built(tmp_path, [], items)
        self._answer(path, "fixed")
        result = self._parsed(path, [], items)
        assert result.bucket(FixOutcome.FIXED) == []
        assert [e.id for e in result.bucket(FixOutcome.FIXED, item=True)] == ["c9"]

    def test_a_section_the_pass_never_handed_over_is_ignored(self, tmp_path):
        """The file is agent-editable — an invented id names nobody to reply to."""
        threads = [self._thread()]
        path = self._built(tmp_path, threads)
        path.write_text(path.read_text() + (
            "\n## <!-- fix:invented --> z.py:1 — @nobody\n\n- [x] fixed\n"
        ))
        result = self._parsed(path, threads)
        assert result.bucket(FixOutcome.FIXED) == []
        assert [e.id for e in result.bucket(FixOutcome.DEFERRED)] == ["t1"]
