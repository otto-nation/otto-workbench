"""Helpers shared by the review document and verdict suites."""

from pathlib import Path


def _write(tmp_path, body: str) -> Path:
    review = tmp_path / "review.md"
    review.write_text(body)
    return review
