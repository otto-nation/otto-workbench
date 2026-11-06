"""Helpers shared by the claims suites: a ClaimEntry builder and a writer dict."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.claim_types import ClaimEntry, ClaimSource, ClaimVerdict


def claim(
    id="C1",
    source=ClaimSource.DESCRIPTION,
    text="retries back off",
    verdict=ClaimVerdict.SUPPORTED,
    evidence="the delay doubles",
    path="a.py",
    snippet="delay *= 2",
    finding="",
):
    """A claim entry, a supported description claim unless the caller says otherwise."""
    return ClaimEntry(
        id=id,
        source=source,
        text=text,
        verdict=verdict,
        evidence=evidence,
        path=path,
        snippet=snippet,
        finding=finding,
    )


CLAIM_DOC = {
    "source": "description",
    "text": "retries back off",
    "verdict": "supported",
    "evidence": "the delay doubles",
    "path": "a.py",
    "snippet": "delay *= 2",
    "finding": "",
}
