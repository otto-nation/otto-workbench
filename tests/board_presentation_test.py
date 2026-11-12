"""board.presentation against the golden fixture."""

import json
from pathlib import Path

import pytest

import board.columns
import board.presentation
import core.serde
from board.model import CiStatus, Mergeable, PrState, ReviewRunRef, Row, StatusKind
from board.presentation import BatchState, BatchTone

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "board_presentation.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = DATA["cases"]


def _pr(case: dict) -> dict:
    return {**DATA["base"], **case["pr"]}


def _row(pr: dict) -> Row:
    """The fixture's JS record (``_lib/github.js`` names) as the Row it stands for."""
    return Row(
        repo=pr["repoSlug"],
        number=pr["number"],
        title=pr["title"],
        author=pr["author"],
        branch=pr["branch"],
        base_branch=pr["baseBranch"],
        labels=tuple(pr["labels"]),
        additions=pr["additions"],
        deletions=pr["deletions"],
        changed_files=pr["changedFiles"],
        is_draft=pr["isDraft"],
        state=PrState(pr["state"]),
        in_merge_queue=pr["inMergeQueue"],
        mergeable=Mergeable(pr["mergeable"]),
        ci_status=CiStatus(pr["ciStatus"]),
        ci_unknown=pr["ciUnknown"],
        hydrated=pr["hydrated"],
        review_decision=pr["reviewDecision"],
        my_review_state=pr["myReviewState"],
        is_requested_reviewer=pr["isRequestedReviewer"],
        has_new_changes_since_review=pr["hasNewChangesSinceReview"],
        review_runs=tuple(ReviewRunRef(posted_event=r["postedEvent"]) for r in pr["reviewRuns"]),
        busy=pr["busy"],
        owed=pr["owedFeedback"],
    )


def _batch(case: dict) -> BatchState | None:
    state = case["batch_state"]
    if state is None:
        return None
    return BatchState(text=state["text"], tone=BatchTone(state["tone"]))


def _wire(value) -> object:
    return json.loads(json.dumps(core.serde.to_dict(value)))


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_present_matches_the_golden_case(case):
    pr = _pr(case)
    got = board.presentation.present(_row(pr), reviewing=pr["reviewing"], batch=_batch(case))
    assert _wire(got) == case["presentation"]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_lanes_match_the_golden_case(case):
    pr = _pr(case)
    lanes = board.columns.lanes(_row(pr))
    team = lanes.team_reviewing if pr["reviewing"] else lanes.team
    mine = lanes.mine_reviewing if pr["reviewing"] else lanes.mine
    assert {"team": team.value, "mine": mine.value} == case["lanes"]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_document_presentation_is_present_without_client_context(case):
    pr = _pr(case)
    got = board.presentation.present(_row(pr))
    assert _wire(got) == case["document_presentation"]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_document_lanes_are_lanes_of_the_row(case):
    pr = _pr(case)
    assert _wire(board.columns.lanes(_row(pr))) == case["document_lanes"]


def test_cards_and_rows_never_carry_a_pass_status():
    for case in CASES:
        pr = _pr(case)
        got = board.presentation.present(_row(pr), reviewing=pr["reviewing"], batch=_batch(case))
        shown = got.badges + got.row_badges
        assert all(badge.status is not StatusKind.PASS for badge in shown), case["name"]


def test_the_document_passes_no_client_context():
    row = _row(_pr(CASES[0]))
    assert board.presentation.present(row) == board.presentation.present(
        row, reviewing=False, batch=None,
    )
