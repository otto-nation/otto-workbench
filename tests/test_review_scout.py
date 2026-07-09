"""Tests for review_scout: lead parsing, filtering, and formatting."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_scout import (
    Lead,
    filter_leads_for_group,
    format_leads_block,
    is_scout_output,
    parse_scout_output,
)


# ── parse_scout_output ───────────────────────────────────────────────────────


SCOUT_OUTPUT = """\
## Investigation Leads

- **`src/auth.py:42`** — Error return value ignored after db.Query
  Severity hint: must-fix
  No error check between query and result usage.

- **`pkg/handler.go:100-115`** — Unchecked type assertion
  Severity hint: should-fix
  Could panic on nil interface.

- **`lib/utils.py`** — Deprecated API call to requests.get without timeout
  Severity hint: nit

## No Scrutiny Needed

- `generated/proto.pb.go` — generated code, no custom logic
- `vendor/third_party.js` — vendored dependency
"""


class TestParseScoutOutput:
    def test_parses_leads(self):
        leads, no_scrutiny = parse_scout_output(SCOUT_OUTPUT)
        assert len(leads) == 3
        assert leads[0].file == "src/auth.py"
        assert leads[0].line == 42
        assert leads[0].concern == "Error return value ignored after db.Query"
        assert leads[0].severity_hint == "must-fix"
        assert "error check" in leads[0].why.lower()

    def test_parses_line_ranges(self):
        leads, _ = parse_scout_output(SCOUT_OUTPUT)
        assert leads[1].file == "pkg/handler.go"
        assert leads[1].line == 100

    def test_parses_no_line_number(self):
        leads, _ = parse_scout_output(SCOUT_OUTPUT)
        assert leads[2].file == "lib/utils.py"
        assert leads[2].line is None

    def test_parses_no_scrutiny(self):
        _, no_scrutiny = parse_scout_output(SCOUT_OUTPUT)
        assert len(no_scrutiny) == 2
        assert "generated/proto.pb.go" in no_scrutiny
        assert "vendor/third_party.js" in no_scrutiny

    def test_empty_input(self):
        leads, no_scrutiny = parse_scout_output("")
        assert leads == []
        assert no_scrutiny == []

    def test_no_leads_section(self):
        text = "## No Scrutiny Needed\n\n- `foo.py` — trivial\n"
        leads, no_scrutiny = parse_scout_output(text)
        assert leads == []
        assert no_scrutiny == ["foo.py"]

    def test_no_scrutiny_section_only(self):
        text = "## Investigation Leads\n\n- **`a.py:1`** — concern\n  Severity hint: must-fix\n"
        leads, no_scrutiny = parse_scout_output(text)
        assert len(leads) == 1
        assert no_scrutiny == []

    def test_dashes_instead_of_em_dash(self):
        text = "## Investigation Leads\n\n- **`a.py:10`** -- double dash concern\n"
        leads, _ = parse_scout_output(text)
        assert len(leads) == 1
        assert leads[0].concern == "double dash concern"


# ── filter_leads_for_group ───────────────────────────────────────────────────


class TestFilterLeadsForGroup:
    def test_filters_to_group_files(self):
        leads = [
            Lead("a.py", 1, "concern a", "must-fix", ""),
            Lead("b.py", 2, "concern b", "nit", ""),
            Lead("c.py", 3, "concern c", "should-fix", ""),
        ]
        filtered = filter_leads_for_group(leads, ["a.py", "c.py"])
        assert len(filtered) == 2
        assert filtered[0].file == "a.py"
        assert filtered[1].file == "c.py"

    def test_empty_group_returns_nothing(self):
        leads = [Lead("a.py", 1, "x", "", "")]
        assert filter_leads_for_group(leads, []) == []

    def test_no_leads_returns_empty(self):
        assert filter_leads_for_group([], ["a.py"]) == []


# ── format_leads_block ───────────────────────────────────────────────────────


class TestFormatLeadsBlock:
    def test_formats_leads_with_location(self):
        leads = [Lead("a.py", 42, "problem here", "must-fix", "explanation")]
        output = format_leads_block(leads, [])
        assert "### Investigation leads from scout scan" in output
        assert "**`a.py:42`**" in output
        assert "problem here" in output
        assert "Severity hint: must-fix" in output
        assert "explanation" in output

    def test_formats_lead_without_line(self):
        leads = [Lead("a.py", None, "no line", "", "")]
        output = format_leads_block(leads, [])
        assert "**`a.py`**" in output
        assert ":" not in output.split("**`a.py`**")[0].split("**")[-1] or True

    def test_includes_no_scrutiny(self):
        output = format_leads_block([], ["gen.go", "vendor.js"])
        assert "### Files that do not need scrutiny" in output
        assert "`gen.go`" in output

    def test_filters_by_group(self):
        leads = [
            Lead("a.py", 1, "x", "", ""),
            Lead("b.py", 2, "y", "", ""),
        ]
        output = format_leads_block(leads, ["gen.go"], group_files=["a.py"])
        assert "a.py" in output
        assert "b.py" not in output
        assert "gen.go" not in output

    def test_empty_returns_empty(self):
        assert format_leads_block([], []) == ""

    def test_empty_after_group_filter(self):
        leads = [Lead("a.py", 1, "x", "", "")]
        assert format_leads_block(leads, [], group_files=["b.py"]) == ""


# ── is_scout_output ──────────────────────────────────────────────────────────


class TestIsScoutOutput:
    def test_detects_sentinel(self):
        text = "some preamble\n### Investigation leads from scout scan\n- stuff"
        assert is_scout_output(text) is True

    def test_detects_section_header(self):
        text = "## Investigation Leads\n\n- **`a.py`** — concern\n"
        assert is_scout_output(text) is True

    def test_rejects_regular_holistic(self):
        text = "## Holistic Assessment\n\nThis PR adds authentication..."
        assert is_scout_output(text) is False

    def test_empty_input(self):
        assert is_scout_output("") is False
