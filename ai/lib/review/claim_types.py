"""Claims are what a PR's description or linked ticket asserts about the change.

A writer reports each with a verdict, and the harness numbers them `C1…`.
`supported` holds; `contradicted` is shown false; `unsupported` is checkable
but nothing in the code shows it; `unverifiable` is not statically checkable,
or its cited code did not verify. `evidence` is prose. `path` and `snippet`
are the code the verdict rests on, and are required for supported and
contradicted. `finding` is optional: the id of a writer finding, of any
severity, that covers the claim.
"""

# doc-group: findings

from dataclasses import dataclass
from enum import StrEnum


class ClaimSource(StrEnum):
    DESCRIPTION = "description"
    TICKET = "ticket"


class ClaimVerdict(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNSUPPORTED = "unsupported"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class ClaimReport:
    """What an agent writes; harness numbering is absent."""

    source: ClaimSource
    text: str
    verdict: ClaimVerdict
    evidence: str
    path: str = ""
    snippet: str = ""
    finding: str = ""


@dataclass(frozen=True)
class ClaimEntry:
    """A claim as stored on the record; `id` is `C1…`."""

    id: str
    source: ClaimSource
    text: str
    verdict: ClaimVerdict
    evidence: str
    path: str = ""
    snippet: str = ""
    finding: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", ClaimSource(self.source))
        object.__setattr__(self, "verdict", ClaimVerdict(self.verdict))
