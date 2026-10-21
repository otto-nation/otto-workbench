"""Tests for review.scout: lead parsing, filtering, and formatting."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review.artifacts import HolisticArtifact, Lead, QuietFile, ScoutArtifact, SeverityHint
from review.scout import holistic_context, scout_context


def test_leads_render_with_their_location_hint_and_reason():
    text = scout_context(ScoutArtifact(
        leads=(Lead("a.go", 4, 9, "error dropped", SeverityHint.MUST_FIX, "no check"),),
        no_scrutiny=(QuietFile("api.pb.go", "generated"),),
    ))
    assert text.startswith("## Scout context\n### Investigation leads from scout scan")
    assert "- **`a.go:4-9`** — error dropped\n  Severity hint: must-fix. no check" in text
    assert "- `api.pb.go` — generated" in text


def test_a_direction_concern_reaches_the_context():
    """D13: written by the scout and, until now, read by nothing."""
    text = scout_context(ScoutArtifact(direction_concern="Use the existing queue."))
    assert "### Direction concern\n\nUse the existing queue." in text


def test_an_empty_scan_renders_no_context():
    assert scout_context(ScoutArtifact()) == ""


def test_the_holistic_context_carries_every_section():
    text = holistic_context(HolisticArtifact(
        assessment="Does what it says.", flags=("watch retry",), cross_module=("units differ",),
        direction_concern="Wrong layer.",
    ))
    assert text.startswith("## Holistic context")
    for part in ("Does what it says.", "### Flags for detailed review\n- watch retry",
                 "### Cross-module concerns\n- units differ", "### Direction concern\nWrong layer."):
        assert part in text
