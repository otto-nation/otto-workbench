"""Tests for review_static_analysis: static analysis framework for review pipeline."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))
if str(REPO_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "lib"))

from review_static_analysis import (
    CheckerResult,
    StaticViolation,
    check_nesting_depth,
    format_static_analysis,
    inject_static_analysis,
)


class TestFormatStaticAnalysis:
    def test_empty_results_returns_empty_string(self):
        assert format_static_analysis([]) == ""

    def test_all_checkers_pass(self):
        results = [CheckerResult(name="Nesting depth", violations=[], files_checked=3)]
        output = format_static_analysis(results)
        assert "## Static Analysis" in output
        assert "All checks passed" in output

    def test_violations_present(self):
        violations = [
            StaticViolation(file="bin/my-script", line=42, message="depth 3 exceeds limit 2", context="in process_items()"),
            StaticViolation(file="lib/helper.py", line=15, message="depth 3 exceeds limit 2", context="in validate()"),
        ]
        results = [CheckerResult(name="Nesting depth", violations=violations, files_checked=5)]
        output = format_static_analysis(results)
        assert "## Static Analysis" in output
        assert "### Nesting depth" in output
        assert "2 violations in 2 of 5 files checked" in output
        assert "**`bin/my-script:42`**" in output
        assert "in process_items()" in output
        assert "**`lib/helper.py:15`**" in output

    def test_violation_without_context(self):
        violations = [
            StaticViolation(file="script.sh", line=10, message="depth 3 exceeds limit 2"),
        ]
        results = [CheckerResult(name="Nesting depth", violations=violations, files_checked=1)]
        output = format_static_analysis(results)
        assert "**`script.sh:10`** — depth 3 exceeds limit 2" in output
        assert "(in " not in output

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


class TestInjectStaticAnalysis:
    def test_injects_before_verdict(self):
        review = "## Summary\nLooks good.\n\n## Must fix\n- [M1] bug\n\n## Verdict\nApprove"
        section = "## Static Analysis\n\nAll checks passed."
        result = inject_static_analysis(review, section)
        assert result.index("## Static Analysis") < result.index("## Verdict")
        assert "## Must fix" in result

    def test_appends_when_no_verdict(self):
        review = "## Summary\nLooks good.\n\n## Must fix\n- [M1] bug"
        section = "## Static Analysis\n\nAll checks passed."
        result = inject_static_analysis(review, section)
        assert result.endswith(section)

    def test_empty_section_returns_unchanged(self):
        review = "## Summary\nLooks good."
        result = inject_static_analysis(review, "")
        assert result == review


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
