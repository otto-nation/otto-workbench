"""Typed request union, parser, drain order, and Run.cancel serde."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.requests  # noqa: E402
import batch.store  # noqa: E402
import core.serde  # noqa: E402
from batch.requests import Cancel, Decide, Envelope, Kind, Malformed, Release  # noqa: E402

RID = "20261014T120000-abcd"


def _run():
    item = batch.model.Item(
        key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="aaa")
    return batch.model.Run(
        id=RID, started_at="t", steps=[], pool=1, auto_publish=[], items=[item])


def test_kindless_dict_parses_as_decide():
    assert batch.requests.parse({"decision": "d1", "action": "retry"}) == Decide(
        "d1", "retry")


def test_kind_decide_parses_as_decide():
    assert batch.requests.parse(
        {"kind": "decide", "decision": "d1", "action": "accept", "reason": "r",
         "body_file": "/b", "commit": "abc"}) == Decide(
        "d1", "accept", reason="r", body_file="/b", commit="abc")


def test_kind_release_parses_item_and_kill():
    assert batch.requests.parse(
        {"kind": "release", "item": "o/r#1"}) == Release("o/r#1")
    assert batch.requests.parse(
        {"kind": "release", "item": "o/r#1", "kill": True}) == Release("o/r#1", kill=True)


def test_kind_cancel_parses_kill_as_bool():
    assert batch.requests.parse({"kind": "cancel"}) == Cancel()
    assert batch.requests.parse({"kind": "cancel", "kill": True}) == Cancel(kill=True)
    assert batch.requests.parse({"kind": "cancel", "kill": 0}) == Cancel(kill=False)


@pytest.mark.parametrize("raw,needle", [
    ("nope", "not an object"),
    (["x"], "not an object"),
    ({"kind": "nope"}, "unknown kind"),
    ({"action": "retry"}, "decision"),
    ({"decision": "d1"}, "action"),
    ({"kind": "decide", "decision": "d1"}, "action"),
    ({"decision": 1, "action": "retry"}, "decision"),
    ({"decision": "d1", "action": 1}, "action"),
    ({"kind": "release"}, "item"),
    ({"kind": "release", "item": 1}, "item"),
])
def test_parse_malformed_names_the_problem(raw, needle):
    got = batch.requests.parse(raw)
    assert isinstance(got, Malformed)
    assert needle in got.reason


def test_to_dict_always_carries_kind():
    assert batch.requests.to_dict(Decide("d1", "retry"))["kind"] == Kind.DECIDE.value
    assert batch.requests.to_dict(Cancel(kill=True)) == {
        "kind": Kind.CANCEL.value, "kill": True}
    assert batch.requests.to_dict(Release("o/r#1", kill=True)) == {
        "kind": Kind.RELEASE.value, "item": "o/r#1", "kill": True}


def test_to_dict_round_trips_through_parse():
    decide = Decide("d1", "reply", body_file="/b")
    cancel = Cancel(kill=True)
    release = Release("o/r#1", kill=True)
    assert batch.requests.parse(batch.requests.to_dict(decide)) == decide
    assert batch.requests.parse(batch.requests.to_dict(cancel)) == cancel
    assert batch.requests.parse(batch.requests.to_dict(release)) == release


def test_drain_order_puts_every_cancel_first_then_sorts_by_name():
    envelopes = [
        Envelope("b.json", Decide("d1", "publish")),
        Envelope("a.json", Cancel()),
        Envelope("c.json", Cancel(kill=True)),
        Envelope("d.json", Malformed("x")),
        Envelope("e.json", Decide("d2", "retry")),
        Envelope("f.json", Release("o/r#1")),
    ]
    ordered = batch.requests.drain_order(envelopes)
    assert [e.name for e in ordered] == [
        "a.json", "c.json", "b.json", "d.json", "e.json", "f.json"]
    assert isinstance(ordered[0].request, Cancel)
    assert isinstance(ordered[1].request, Cancel)


def test_outcome_ok_is_the_absence_of_error():
    req = Decide("d1", "retry")
    assert batch.requests.Outcome(name="n", request=req).ok
    assert not batch.requests.Outcome(name="n", request=req, error="nope").ok


def test_cancel_state_round_trips_through_the_state_file():
    run = _run()
    run.cancel = batch.model.CancelState(kill=True, requested_at="t0")
    # Written directly: no session is needed to exercise load.
    core.serde.write_json(batch.store.run_dir(RID) / batch.store.STATE_FILE,
                          core.serde.to_dict(run))
    back = batch.store.load(RID)
    assert back.cancel == batch.model.CancelState(kill=True, requested_at="t0")
    assert core.serde.to_dict(back)["cancel"] == {"kill": True, "requested_at": "t0"}


def test_state_file_saved_before_cancel_existed_still_loads():
    run = _run()
    # Written directly: no session is needed to exercise load.
    path = batch.store.run_dir(RID) / batch.store.STATE_FILE
    data = core.serde.to_dict(run)
    data.pop("cancel", None)
    core.serde.write_json(path, data)
    assert batch.store.load(RID).cancel is None
