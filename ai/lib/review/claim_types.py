"""Claims are what a PR's description or linked ticket asserts about the change.

A writer reports each with a verdict, and the harness numbers them `C1…`.
`supported` holds; `contradicted` is shown false; `unsupported` is checkable
but nothing in the code shows it; `unverifiable` is not statically checkable,
or its cited code did not verify. `evidence` is prose. `path` and `snippet`
are the code the verdict rests on, and are required for supported and
contradicted. `finding` is optional: the id of a writer finding, of any
severity, that covers the claim. This module is a leaf: it imports nothing
from `review`. `ClaimReport` is what an agent writes; `ClaimEntry` is what
the record stores, with harness numbering.
"""

# doc-group: findings

import hashlib
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


# Verdicts that must cite the code they rest on.
CITE_VERDICTS = frozenset({ClaimVerdict.SUPPORTED, ClaimVerdict.CONTRADICTED})


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

    @property
    def key(self) -> str:
        """Eight-hex identity: source and collapsed lower-cased text.

        Verdict, path, snippet and evidence are left out so a verdict flip or a
        re-cited snippet is the same claim. A wording edit is a new claim.
        """
        payload = (
            f"claim\0{self.source.value}\0{' '.join(self.text.split()).lower()}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:8]
