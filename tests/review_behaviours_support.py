"""Helpers shared by the behaviours suites: a BehaviourEntry builder and a writer dict."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.behaviour_types import BehaviourEntry


def behaviour(
    id="B1",
    text="retries back off",
    test_cited="tests/a_test.py::test_backoff",
    paths=("a.py",),
    note="",
):
    """A behaviour entry, a cited backoff on `a.py` unless the caller says otherwise."""
    return BehaviourEntry(
        id=id, text=text, test_cited=test_cited, paths=paths, note=note,
    )


BEHAVIOUR_DOC = {
    "text": "retries back off",
    "test_cited": "tests/a_test.py::test_backoff",
    "paths": ("a.py",),
}
