"""Tests for review.static_analysis: static analysis framework for review pipeline."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))
if str(REPO_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "lib"))

from review.grammar import FINDING_ID_RE
from review.static_analysis import (
    STATIC_ID_RE,
    CheckerResult,
    StaticViolation,
    added_lines,
    all_violations,
    check_nesting_depth,
    format_static_analysis,
    run_static_analysis,
)


class TestFormatStaticAnalysis:
    def test_empty_results_returns_empty_string(self):
        assert format_static_analysis([]) == ""

    def test_all_checkers_pass(self):
        results = [CheckerResult(name="Nesting depth", violations=[], files_checked=3)]
        assert format_static_analysis(results) == "All checks passed."

    def test_body_carries_no_heading(self):
        results = [CheckerResult(
            name="Nesting depth",
            violations=[StaticViolation(file="a.sh", line=1, message="too deep")],
            files_checked=1,
        )]
        assert "## Static Analysis" not in format_static_analysis(results)

    def test_violations_present(self):
        violations = [
            StaticViolation(file="bin/my-script", line=42, message="depth 3 exceeds limit 2", context="in process_items()"),
            StaticViolation(file="lib/helper.py", line=15, message="depth 3 exceeds limit 2", context="in validate()"),
        ]
        results = [CheckerResult(name="Nesting depth", violations=violations, files_checked=5)]
        output = format_static_analysis(results)
        assert "### Nesting depth" in output
        assert "2 violations in 2 of 5 files checked" in output
        assert "**`bin/my-script:42`**" in output
        assert "in process_items()" in output
        assert "**`lib/helper.py:15`**" in output

    def test_violations_wrapped_in_collapsed_details(self):
        violations = [
            StaticViolation(file="a.sh", line=1, message="depth 3 exceeds limit 2"),
            StaticViolation(file="b.sh", line=2, message="depth 3 exceeds limit 2"),
        ]
        results = [CheckerResult(name="Nesting depth", violations=violations, files_checked=2)]
        output = format_static_analysis(results)
        assert "<details>" in output
        assert "<details open>" not in output
        assert "<summary>Static Analysis (2 violations)</summary>" in output
        assert output.rstrip().endswith("</details>")
        # Checker name is a sub-header nested inside the collapsed block
        assert output.index("<summary>") < output.index("### Nesting depth")
        assert output.index("### Nesting depth") < output.index("</details>")

    def test_single_violation_summary_is_singular(self):
        results = [CheckerResult(
            name="Nesting depth",
            violations=[StaticViolation(file="a.sh", line=1, message="too deep")],
            files_checked=1,
        )]
        assert "<summary>Static Analysis (1 violation)</summary>" in format_static_analysis(results)

    def test_all_checks_passed_is_not_collapsed(self):
        results = [CheckerResult(name="Nesting depth", violations=[], files_checked=3)]
        assert "<details>" not in format_static_analysis(results)

    def test_details_block_has_blank_line_after_summary(self):
        results = [CheckerResult(
            name="Nesting depth",
            violations=[StaticViolation(file="a.sh", line=1, message="too deep")],
            files_checked=1,
        )]
        output = format_static_analysis(results)
        lines = output.split("\n")
        summary_idx = next(i for i, line in enumerate(lines) if line.startswith("<summary>"))
        assert lines[summary_idx + 1] == ""

    def test_violation_without_context(self):
        violations = [
            StaticViolation(file="script.sh", line=10, message="depth 3 exceeds limit 2"),
        ]
        results = [CheckerResult(name="Nesting depth", violations=violations, files_checked=1)]
        output = format_static_analysis(results)
        assert "**`script.sh:10`** — depth 3 exceeds limit 2" in output
        assert "(in " not in output

    def test_an_addressable_violation_renders_a_box_and_an_id(self):
        """The box is what the fix pass ticks; the id is what it keys its outcome by."""
        violations = [StaticViolation(
            file="a.sh", line=1, message="depth 3 exceeds limit 2", id="SA1",
        )]
        results = [CheckerResult(name="Nesting depth", violations=violations, files_checked=1)]
        line = next(
            ln for ln in format_static_analysis(results).split("\n")
            if "a.sh" in ln
        )
        assert line.startswith("- [ ] **[SA1]** ")
        assert STATIC_ID_RE.match(line)

    def test_a_violation_with_no_id_renders_without_a_box(self):
        """It never went through `run_static_analysis`, so nothing can answer it.

        Rendering a box the fix pass will never tick would report the violation
        as work in hand when no outcome can reach it.
        """
        violations = [StaticViolation(file="a.sh", line=1, message="too deep")]
        results = [CheckerResult(name="Nesting depth", violations=violations, files_checked=1)]
        line = next(
            ln for ln in format_static_analysis(results).split("\n")
            if "a.sh" in ln
        )
        assert line.startswith("- **`a.sh:1`**")
        assert not STATIC_ID_RE.match(line)

    def test_multiple_checkers_mixed(self):
        results = [
            CheckerResult(name="Checker A", violations=[], files_checked=2),
            CheckerResult(name="Checker B", violations=[
                StaticViolation(file="f.py", line=1, message="bad"),
            ], files_checked=1),
        ]
        output = format_static_analysis(results)
        assert "### Checker B" in output
        assert "Checker A" not in output

    def test_multiple_violations_same_file(self):
        violations = [
            StaticViolation(file="f.sh", line=5, message="depth 3 exceeds limit 2", context="in deep_func()"),
            StaticViolation(file="f.sh", line=8, message="depth 4 exceeds limit 2", context="in deep_func()"),
        ]
        results = [CheckerResult(name="Test", violations=violations, files_checked=1)]
        output = format_static_analysis(results)
        assert "2 violations in 1 of 1 files checked" in output


class TestViolationIds:
    """The ids that let the fix pass take a violation as work."""

    def _results(self, *violations):
        return [CheckerResult(
            name="Nesting depth", violations=list(violations), files_checked=1,
        )]

    def test_ids_are_assigned_in_reading_order(self):
        results = self._results(
            StaticViolation(file="b.py", line=1, message="x"),
            StaticViolation(file="a.py", line=9, message="x"),
            StaticViolation(file="a.py", line=2, message="x"),
        )
        for n, violation in enumerate(all_violations(results), start=1):
            violation.id = f"SA{n}"
        assert [(v.file, v.line, v.id) for v in all_violations(results)] == [
            ("a.py", 2, "SA1"), ("a.py", 9, "SA2"), ("b.py", 1, "SA3"),
        ]

    def test_run_static_analysis_numbers_every_violation(self, tmp_path):
        (tmp_path / "deep.py").write_text(
            "def func():\n"
            "    if True:\n"
            "        for x in range(10):\n"
            "            while True:\n"
            "                pass\n"
        )
        results = run_static_analysis(["deep.py"], str(tmp_path))
        violations = all_violations(results)
        assert violations
        assert [v.id for v in violations] == [
            f"SA{n}" for n in range(1, len(violations) + 1)
        ]

    def test_numbering_runs_across_checkers_rather_than_restarting(self):
        """Two checkers numbering their own would hand two violations one id."""
        results = [
            CheckerResult(name="A", violations=[
                StaticViolation(file="a.py", line=1, message="x"),
            ], files_checked=1),
            CheckerResult(name="B", violations=[
                StaticViolation(file="b.py", line=1, message="x"),
            ], files_checked=1),
        ]
        for n, violation in enumerate(all_violations(results), start=1):
            violation.id = f"SA{n}"
        ids = [v.id for r in results for v in r.violations]
        assert len(set(ids)) == len(ids)

    def test_each_checker_s_block_carries_contiguous_ids(self):
        """The section renders one block per checker, so numbering follows that.

        Numbering by a global `(file, line)` sort while displaying per checker
        interleaves the ids — one block reads `SA1, SA3` and the next `SA2,
        SA4`. Each id is still correct and addressable; it just reads as a
        numbering bug to everyone who opens the review.

        The filenames here are chosen so a global sort and a per-checker sort
        disagree: by `(file, line)` alone the interleaving would be a.py, b.py,
        c.py, d.py across the two checkers.
        """
        results = [
            CheckerResult(name="A", violations=[
                StaticViolation(file="a.py", line=1, message="x"),
                StaticViolation(file="c.py", line=1, message="x"),
            ], files_checked=2),
            CheckerResult(name="B", violations=[
                StaticViolation(file="b.py", line=1, message="x"),
                StaticViolation(file="d.py", line=1, message="x"),
            ], files_checked=2),
        ]
        for n, violation in enumerate(all_violations(results), start=1):
            violation.id = f"SA{n}"

        assert [v.id for v in results[0].violations] == ["SA1", "SA2"]
        assert [v.id for v in results[1].violations] == ["SA3", "SA4"]

    def test_the_rendered_section_numbers_top_to_bottom(self):
        """What a reader sees: ids ascending down the page, no gaps."""
        results = [
            CheckerResult(name="A", violations=[
                StaticViolation(file="a.py", line=1, message="x"),
                StaticViolation(file="c.py", line=1, message="x"),
            ], files_checked=2),
            CheckerResult(name="B", violations=[
                StaticViolation(file="b.py", line=1, message="x"),
                StaticViolation(file="d.py", line=1, message="x"),
            ], files_checked=2),
        ]
        for n, violation in enumerate(all_violations(results), start=1):
            violation.id = f"SA{n}"

        rendered = [
            STATIC_ID_RE.match(ln).group(2)
            for ln in format_static_analysis(results).split("\n")
            if STATIC_ID_RE.match(ln)
        ]
        assert rendered == ["SA1", "SA2", "SA3", "SA4"]

    def test_a_static_id_is_not_readable_as_a_finding(self):
        """The two work streams must not claim each other's ids.

        `FINDING_ID_RE` wants digits straight after the severity key, and the
        `A` in `SA1` is not one — a property of a pattern in another module,
        which is why it is asserted here rather than described in a comment.
        """
        line = "- [ ] **[SA1]** **`a.py:1`** — depth 5 exceeds limit 4"
        assert STATIC_ID_RE.match(line)
        assert not FINDING_ID_RE.match(line)

    def test_a_finding_line_is_not_readable_as_a_violation(self):
        line = "- [ ] **[S1]** `a.py:1` — the guard is missing"
        assert FINDING_ID_RE.match(line)
        assert not STATIC_ID_RE.match(line)

    def test_an_id_quoted_mid_line_is_not_a_declaration(self):
        """Anchored at the head, so prose about a violation does not become one."""
        assert not STATIC_ID_RE.match("- [ ] **[S1]** the fix for **[SA1]** is owed")


class TestCheckNestingDepth:
    def test_no_applicable_files(self, tmp_path):
        result = check_nesting_depth(["README.md", "go.sum"], str(tmp_path))
        assert result is None

    def test_clean_files(self, tmp_path):
        script = tmp_path / "clean.sh"
        script.write_text("#!/bin/bash\necho hello\n")
        result = check_nesting_depth(["clean.sh"], str(tmp_path))
        assert result is not None
        assert result.name == "Nesting depth"
        assert result.violations == []
        assert result.files_checked == 1

    def test_violation_detected(self, tmp_path):
        script = tmp_path / "deep.sh"
        script.write_text(
            "#!/bin/bash\n"
            "func() {\n"
            "  if true; then\n"
            "    for x in a; do\n"
            "      while true; do\n"
            "        echo deep\n"
            "      done\n"
            "    done\n"
            "  fi\n"
            "}\n"
        )
        result = check_nesting_depth(["deep.sh"], str(tmp_path))
        assert result is not None
        assert len(result.violations) > 0
        v = result.violations[0]
        assert v.file == "deep.sh"
        assert "depth" in v.message
        assert "exceeds limit" in v.message
        assert v.context == "in func()"

    def test_python_file(self, tmp_path):
        script = tmp_path / "deep.py"
        script.write_text(
            "def func():\n"
            "    if True:\n"
            "        for x in range(10):\n"
            "            while True:\n"
            "                pass\n"
        )
        result = check_nesting_depth(["deep.py"], str(tmp_path))
        assert result is not None
        assert len(result.violations) > 0

    def test_missing_file_skipped(self, tmp_path):
        result = check_nesting_depth(["nonexistent.sh"], str(tmp_path))
        assert result is not None
        assert result.files_checked == 0
        assert result.violations == []

    def test_mixed_files(self, tmp_path):
        clean = tmp_path / "clean.sh"
        clean.write_text("#!/bin/bash\necho ok\n")
        result = check_nesting_depth(["clean.sh", "README.md", "image.png"], str(tmp_path))
        assert result is not None
        assert result.files_checked == 1

    def test_go_file(self, tmp_path):
        script = tmp_path / "deep.go"
        script.write_text(
            "package main\n\n"
            "func f() {\n"
            "\tif true {\n"
            "\t\tfor i := 0; i < 10; i++ {\n"
            "\t\t\tswitch {\n"
            "\t\t\tdefault:\n"
            "\t\t\t\tprintln()\n"
            "\t\t\t}\n"
            "\t\t}\n"
            "\t}\n"
            "}\n"
        )
        result = check_nesting_depth(["deep.go"], str(tmp_path))
        assert result is not None
        assert len(result.violations) > 0


class TestAddedLinesUnreadableBase:
    """A base git cannot resolve — a shallow fetch that never brought it down
    — reports why rather than leaving the caller to guess from a bare None."""

    def test_an_unresolvable_base_logs_the_git_failure(self, tmp_path, capsys):
        assert added_lines(str(tmp_path), "origin/does-not-exist") is None
        err = capsys.readouterr().err
        assert "added_lines" in err
        assert "origin/does-not-exist" in err
