import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "ai" / "claude" / "bin" / "validate-review-positions"


# --- Load the module without a .py extension ---

@pytest.fixture(scope="session")
def vp():
    import importlib.machinery
    import importlib.util

    loader = importlib.machinery.SourceFileLoader(
        "validate_review_positions", str(SCRIPT)
    )
    spec = importlib.util.spec_from_loader("validate_review_positions", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["validate_review_positions"] = mod
    spec.loader.exec_module(mod)
    yield mod
    del sys.modules["validate_review_positions"]


# --- Helpers ---

SAMPLE_DIFF = textwrap.dedent("""\
    diff --git a/src/app.py b/src/app.py
    index abc1234..def5678 100644
    --- a/src/app.py
    +++ b/src/app.py
    @@ -10,4 +10,6 @@ def hello():
         pass
    +    x = 1
    +    y = 2
         return True
    @@ -30,2 +32,3 @@ def goodbye():
         pass
    +    z = 3
    diff --git a/src/util.py b/src/util.py
    index 1111111..2222222 100644
    --- a/src/util.py
    +++ b/src/util.py
    @@ -5 +5 @@ def helper():
    -    old = 1
    +    new = 1
""")


def _run_cli(tmp_path, diff_text, findings_json):
    """Run the validate-review-positions script as a subprocess."""
    diff_file = tmp_path / "test.diff"
    diff_file.write_text(diff_text)

    review_file = tmp_path / "findings.json"
    review_file.write_text(findings_json)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--diff", str(diff_file), "--review", str(review_file)],
        capture_output=True,
        text=True,
    )
    return result


# --- TestParseDiffHunks ---

class TestParseDiffHunks:
    def test_extracts_file_paths(self, vp):
        hunks = vp.parse_diff_hunks(SAMPLE_DIFF)
        assert set(hunks.keys()) == {"src/app.py", "src/util.py"}

    def test_parses_multiline_hunk_range(self, vp):
        hunks = vp.parse_diff_hunks(SAMPLE_DIFF)
        # @@ -10,4 +10,6 @@ → lines 10..15
        assert (10, 15) in hunks["src/app.py"]

    def test_parses_second_hunk(self, vp):
        hunks = vp.parse_diff_hunks(SAMPLE_DIFF)
        # @@ -30,2 +32,3 @@ → lines 32..34
        assert (32, 34) in hunks["src/app.py"]

    def test_parses_single_line_hunk(self, vp):
        hunks = vp.parse_diff_hunks(SAMPLE_DIFF)
        # @@ -5 +5 @@ → no comma means length=1, so line 5..5
        assert (5, 5) in hunks["src/util.py"]

    def test_empty_diff_returns_empty_dict(self, vp):
        assert vp.parse_diff_hunks("") == {}


# --- TestValidate ---

class TestValidate:
    def test_finding_in_hunk_is_valid(self, vp):
        hunks = {"src/app.py": [(10, 15)]}
        findings = [{"path": "src/app.py", "line": 12, "body": "issue"}]
        valid, file_level, skipped = vp.validate(hunks, findings)
        assert len(valid) == 1
        assert valid[0]["path"] == "src/app.py"
        assert not file_level
        assert not skipped

    def test_finding_outside_hunk_is_file_level(self, vp):
        hunks = {"src/app.py": [(10, 15)]}
        findings = [{"path": "src/app.py", "line": 50, "body": "issue"}]
        valid, file_level, skipped = vp.validate(hunks, findings)
        assert not valid
        assert len(file_level) == 1
        assert "not in any diff hunk" in file_level[0]["reason"]
        assert not skipped

    def test_finding_for_missing_path_is_skipped(self, vp):
        hunks = {"src/app.py": [(10, 15)]}
        findings = [{"path": "src/other.py", "line": 5, "body": "issue"}]
        valid, file_level, skipped = vp.validate(hunks, findings)
        assert not valid
        assert not file_level
        assert len(skipped) == 1
        assert "path not in diff" in skipped[0]["reason"]

    def test_finding_without_line_is_valid(self, vp):
        hunks = {"src/app.py": [(10, 15)]}
        findings = [{"path": "src/app.py", "body": "general comment"}]
        valid, file_level, skipped = vp.validate(hunks, findings)
        assert len(valid) == 1
        assert not file_level
        assert not skipped

    def test_mixed_findings(self, vp):
        hunks = {"src/app.py": [(10, 15)]}
        findings = [
            {"path": "src/app.py", "line": 12, "body": "in hunk"},
            {"path": "src/app.py", "line": 50, "body": "outside hunk"},
            {"path": "src/missing.py", "line": 1, "body": "wrong file"},
        ]
        valid, file_level, skipped = vp.validate(hunks, findings)
        assert len(valid) == 1
        assert len(file_level) == 1
        assert len(skipped) == 1

    def test_empty_findings_list(self, vp):
        hunks = {"src/app.py": [(10, 15)]}
        valid, file_level, skipped = vp.validate(hunks, [])
        assert valid == []
        assert file_level == []
        assert skipped == []


# --- TestCLI ---

class TestCLI:
    def test_valid_findings_exit_0(self, tmp_path):
        findings = [{"path": "src/app.py", "line": 12, "body": "ok"}]
        result = _run_cli(tmp_path, SAMPLE_DIFF, json.dumps(findings))
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert len(output["valid"]) == 1
        assert output["file_level"] == []
        assert output["skipped"] == []

    def test_file_level_findings_exit_1(self, tmp_path):
        findings = [{"path": "src/app.py", "line": 99, "body": "outside"}]
        result = _run_cli(tmp_path, SAMPLE_DIFF, json.dumps(findings))
        assert result.returncode == 1
        output = json.loads(result.stdout)
        assert len(output["file_level"]) == 1

    def test_skipped_findings_exit_2(self, tmp_path):
        findings = [{"path": "nonexistent.py", "line": 1, "body": "gone"}]
        result = _run_cli(tmp_path, SAMPLE_DIFF, json.dumps(findings))
        assert result.returncode == 2
        output = json.loads(result.stdout)
        assert len(output["skipped"]) == 1

    def test_non_array_json_exits_3(self, tmp_path):
        result = _run_cli(tmp_path, SAMPLE_DIFF, json.dumps({"not": "array"}))
        assert result.returncode == 3
        assert "must contain a JSON array" in result.stderr

    def test_help_exits_0(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "validate" in result.stdout.lower()

    def test_empty_findings_array_exits_0(self, tmp_path):
        result = _run_cli(tmp_path, SAMPLE_DIFF, json.dumps([]))
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output == {"valid": [], "file_level": [], "skipped": []}
