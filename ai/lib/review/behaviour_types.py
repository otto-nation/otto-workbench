"""Behaviours are what a change does, listed with the test that holds each.

A writer reports each with an optional cited test (`path` or `path::name`)
and the source paths it covers, and the harness numbers them `B1…`.
`note` is harness-owned: why a cited test was cleared, in fixed text that
echoes nothing from the agent. This module is a leaf: it imports nothing
from `review`. `BehaviourReport` is what an agent writes; `BehaviourEntry`
is what the record stores, with harness numbering.
"""

# doc-group: findings

from dataclasses import dataclass


@dataclass(frozen=True)
class BehaviourReport:
    """What an agent writes; harness numbering and notes are absent."""

    text: str
    test_cited: str = ""
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class BehaviourEntry:
    """A behaviour as stored on the record; `id` is `B1…`."""

    id: str
    text: str
    test_cited: str = ""
    paths: tuple[str, ...] = ()
    note: str = ""
