"""Helpers shared by the review record suites: fixtures and the parity canonicaliser."""

import json
import shutil
import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.types import ReviewMeta, review_meta_from_dict

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "review_record"


def fixture_text(case: str, name: str) -> str:
    return (FIXTURES / case / name).read_text()


def fixture_meta(case: str) -> ReviewMeta:
    return review_meta_from_dict(json.loads(fixture_text(case, "meta.json")))


def expected_text(case: str) -> str:
    """`expected.md` when the case reorders sections (D2/D4), else `input.md`."""
    path = FIXTURES / case / "expected.md"
    return path.read_text() if path.exists() else fixture_text(case, "input.md")


def canonical(md: str) -> str:
    """Parity's comparison form: no blank lines, no trailing space, no banner (D1)."""
    from review.render import BANNER

    return "\n".join(
        line.rstrip() for line in md.splitlines()
        if line.strip() and line.strip() != BANNER
    )


def copy_tree(dest: Path) -> Path:
    """Copy the fixture tree into `dest`, restoring `*.py` names from `*.py.txt`."""
    shutil.copytree(FIXTURES / "tree", dest)
    for path in sorted(dest.rglob("*.txt"), reverse=True):
        path.rename(path.with_suffix(""))
    return dest


def store_review(review_file, text: str, meta=None):
    """A review directory as the pipeline leaves it: review.json, meta.json, review.md."""
    from review.ingest import ingest_review
    from review.store import write_review
    from review.types import ReviewMeta
    review_file = Path(review_file)
    write_review(review_file, ingest_review(text, strict=False), meta or ReviewMeta())
    return review_file


def seed_review(reviews_dir: Path, name: str = "widget-42",
                body: str | None = None, **meta) -> Path:
    """A review directory the reviews walk will classify as a review.

    One builder rather than one per suite, because the on-disk shape — a
    directory holding `review.md` beside a `meta.json` sidecar — is what the
    walk classifies on, and a second copy of it drifts the moment the layout
    moves. `meta.json` is written only when a caller passes attribution, so the
    default stands in for a review predating the sidecar.
    """
    d = reviews_dir / name
    d.mkdir()
    text = "## Must fix\n- **[M1]** a.py:1 — bug\n" if body is None else body
    store_review(
        d / "review.md", text,
        review_meta_from_dict(meta) if meta else ReviewMeta(),
    )
    return d


def written_review(directory: Path) -> Path:
    """A `review.md` on disk holding one Must-fix finding.

    Shared because three review-flow suites each defined a near-identical
    helper for the same on-disk shape — a review file with one finding, which
    is all a caller checking "did the flow leave a review behind" needs.
    Unlike `seed_review`, this takes the review's own directory directly and
    creates any missing parents, since callers here build ad hoc paths rather
    than seeding into an existing `reviews_dir`.
    """
    directory.mkdir(parents=True, exist_ok=True)
    return store_review(directory / "review.md", "## Must fix\n- **[M1]** boom\n")
