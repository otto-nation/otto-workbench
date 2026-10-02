"""fix.comment_replies: the triage queue is recorded."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _no_published_summary  # noqa: E402
import fix.comment_replies
from pr.thread_models import CommentItem


class TestTriageQueueIsRecorded:
    """The flag the drain turns on: a drafted triage owes its replies."""

    def _item(self):
        return CommentItem(id="t1", summary="s", file="x.py", line=1)

    def test_a_drafted_triage_records_what_it_did_not_send(self):
        assert fix.comment_replies.replies_drafted([self._item()], []) is True
        assert fix.comment_replies.replies_drafted([], [self._item()]) is True

    def test_a_published_triage_owes_nothing(self, publishing_on):
        assert fix.comment_replies.replies_drafted([self._item()], [self._item()]) is False

    def test_a_triage_with_no_replies_owes_nothing(self):
        assert fix.comment_replies.replies_drafted([], []) is False
