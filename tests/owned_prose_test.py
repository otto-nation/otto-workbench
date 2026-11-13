"""Pin retyped constants in rules and docs to the values their owners produce."""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.plan  # noqa: E402
import fix.regression  # noqa: E402
import fix.suite  # noqa: E402
import review.fix  # noqa: E402
from core.project_context import CLAUDE_CODE_MIN_VERSION  # noqa: E402
from pr.summary_model import ActionCell  # noqa: E402

SELF_REVIEW = REPO_ROOT / "ai" / "guidelines" / "rules" / "self-review.md"
AI_AUTOMATION = REPO_ROOT / "docs" / "ai-automation.src.md"

_TALLY = "3 fixed, 1 skipped"


def test_self_review_tally_quotes_qualify_tally_timed_out():
    prose = SELF_REVIEW.read_text()
    duration_s = 17.0
    qualified = fix.suite.qualify_tally(
        _TALLY,
        fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.TIMED_OUT, duration_s=duration_s,
        ),
    )
    needle = qualified.replace(f"{duration_s:.0f}s", "{N}s")
    assert needle in prose, needle


def test_self_review_tally_quotes_qualify_tally_error():
    prose = SELF_REVIEW.read_text()
    qualified = fix.suite.qualify_tally(
        _TALLY,
        fix.suite.SuiteResult(status=fix.suite.SuiteStatus.ERROR),
    )
    assert qualified in prose, qualified


def test_self_review_tally_quotes_the_untested_suffix():
    src = inspect.getsource(review.fix.ReviewFixAdapter.landing)
    match = re.search(r'tally \+= f"(\s*\(\{untested\} without a test\))"', src)
    assert match, src
    prose = SELF_REVIEW.read_text()
    table = match.group(1).replace("{untested}", "2")
    narrative = match.group(1).replace("{untested}", "N")
    assert f"{_TALLY}{table}" in prose
    assert narrative.strip() in prose


def test_self_review_quotes_the_untested_detail_prefix():
    prose = SELF_REVIEW.read_text()
    detail = f"{fix.regression.UNTESTED_DETAIL_PREFIX} <file> in this commit"
    assert detail in prose, detail


def test_already_addressed_table_quotes_action_cells():
    prose = AI_AUTOMATION.read_text()
    assert ActionCell.UNATTRIBUTED.value in prose
    assert ActionCell.ALREADY_ADDRESSED.value in prose


def test_ai_automation_quotes_claude_code_min_version():
    prose = AI_AUTOMATION.read_text()
    assert CLAUDE_CODE_MIN_VERSION in prose


def test_ai_automation_does_not_restate_search_page():
    prose = AI_AUTOMATION.read_text()
    assert "ten to a page" not in prose
    assert "ten to a request" not in prose
    assert str(batch.plan._SEARCH_PAGE) not in _batch_paging_paragraph(prose)


def _batch_paging_paragraph(prose: str) -> str:
    start = prose.index("`run --prs`")
    end = prose.index("A PR that needs no", start)
    return prose[start:end]
