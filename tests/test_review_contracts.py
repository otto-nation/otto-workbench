"""Cross-file contract tests for the claude-review system.

Verifies that constants, templates, regex patterns, and CLI interfaces
stay consistent across review_common, review_findings, review_prompt,
review-templates/, and agents/reviewer.md.

All expectations are derived dynamically from source — no hardcoded lists.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
TEMPLATE_DIR = LIB_DIR / "review-templates"
BIN_DIR = REPO_ROOT / "ai" / "claude" / "bin"
AGENTS_DIR = REPO_ROOT / "ai" / "claude" / "agents"

# Insert lib dir so we can import review_common / review_findings directly
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review_common  # noqa: E402
import review_findings  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────


def _collect_template_constants() -> dict[str, str]:
    """Return {constant_name: value} for all TEMPLATE_* constants in review_common."""
    tree = ast.parse((LIB_DIR / "review_common.py").read_text())
    constants: dict[str, str] = {}
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.startswith("TEMPLATE_"):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    constants[target.id] = node.value.value
    return constants


def _template_files() -> set[str]:
    """Return the set of .md filenames in the review-templates directory."""
    return {p.name for p in TEMPLATE_DIR.glob("*.md")}


def _extract_template_vars(template_path: Path) -> set[str]:
    """Extract ${var} placeholder names from a template file."""
    content = template_path.read_text()
    return set(re.findall(r"\$\{(\w+)\}", content))


def _python_scripts_with_shebang() -> list[Path]:
    """Discover Python scripts in bin/ with #!/usr/bin/env python3 shebang."""
    scripts: list[Path] = []
    for path in sorted(BIN_DIR.iterdir()):
        if path.name.startswith("_"):
            continue
        if not path.is_file():
            continue
        try:
            first_line = path.read_text().split("\n", 1)[0]
        except (OSError, UnicodeDecodeError):
            continue
        if first_line.strip() == "#!/usr/bin/env python3":
            scripts.append(path)
    return scripts


# ── 1. TestTemplateFileConsistency ───────────────────────────────────────────


class TestTemplateFileConsistency:
    """Every TEMPLATE_* constant maps to a file and vice versa."""

    def test_every_template_constant_has_a_file(self):
        constants = _collect_template_constants()
        md_constants = {
            name: val for name, val in constants.items() if val.endswith(".md")
        }
        assert md_constants, "No TEMPLATE_* constants ending in .md found"

        files = _template_files()
        missing = {
            name: val for name, val in md_constants.items() if val not in files
        }
        assert not missing, (
            "TEMPLATE_* constants reference files that do not exist:\n"
            + "\n".join(f"  - {name} = {val!r}" for name, val in sorted(missing.items()))
        )

    def test_every_template_file_has_a_constant(self):
        constants = _collect_template_constants()
        constant_values = {val for val in constants.values() if val.endswith(".md")}
        files = _template_files()
        assert files, "No .md files found in review-templates/"

        unreferenced = sorted(files - constant_values)
        assert not unreferenced, (
            "Template files not referenced by any TEMPLATE_* constant:\n"
            + "\n".join(f"  - {f}" for f in unreferenced)
        )


# ── 2. TestSeverityConsistency ───────────────────────────────────────────────


class TestSeverityConsistency:
    """Severity-related constants stay in sync across review_common."""

    def test_finding_sections_cover_all_severity_labels(self):
        severity_keys = set(review_common.SEVERITY_LABELS.keys())
        section_prefixes = {prefix for _, prefix in review_common.FINDING_SECTIONS}
        missing = severity_keys - section_prefixes
        assert not missing, (
            f"SEVERITY_LABELS keys not covered in FINDING_SECTIONS prefixes: {missing}"
        )

    def test_section_headers_cover_all_severities(self):
        severity_keys = set(review_common.SEVERITY_LABELS.keys())
        header_values = set(review_common.SECTION_HEADERS.values())
        missing = severity_keys - header_values
        assert not missing, (
            f"SEVERITY_LABELS keys not covered in SECTION_HEADERS values: {missing}"
        )

    def test_finding_prefix_constants_match_sections(self):
        # Collect all FINDING_PREFIX_* constant values from the module
        prefix_constants = set()
        for name in dir(review_common):
            if name.startswith("FINDING_PREFIX_"):
                prefix_constants.add(getattr(review_common, name))

        section_prefixes = {prefix for _, prefix in review_common.FINDING_SECTIONS}
        assert prefix_constants == section_prefixes, (
            f"FINDING_PREFIX_* values {prefix_constants} != "
            f"FINDING_SECTIONS prefixes {section_prefixes}"
        )


# ── 3. TestFindingIdRegex ────────────────────────────────────────────────────


class TestFindingIdRegex:
    """FINDING_ID_RE matches all expected finding formats."""

    @pytest.mark.parametrize(
        "severity,seq,line",
        [
            ("M", 1, '- **[M1]** **`handler.go:42`** — description'),
            ("S", 3, '- **[S3]** **`api/server.py:10`** — missing validation'),
            ("N", 12, '- **[N12]** **`README.md:1`** — typo'),
            ("I", 5, '- **[I5]** **`config.yaml:99`** — use struct tags'),
        ],
        ids=["must-fix", "should-fix", "nit", "idiom"],
    )
    def test_standard_finding_format(self, severity, seq, line):
        m = review_findings.FINDING_ID_RE.match(line)
        assert m is not None, f"FINDING_ID_RE did not match: {line!r}"
        assert m.group(1) == severity
        assert int(m.group(2)) == seq

    @pytest.mark.parametrize(
        "line",
        [
            '- [ ] **[M1]** **`handler.go:42`** — unchecked error',
            '- [ ] **[S2]** **`api.go:10`** — missing context',
        ],
        ids=["checkbox-M", "checkbox-S"],
    )
    def test_checkbox_format(self, line):
        m = review_findings.FINDING_ID_RE.match(line)
        assert m is not None, f"FINDING_ID_RE did not match checkbox format: {line!r}"

    @pytest.mark.parametrize(
        "line",
        [
            '- ~~**[S1]** **`old.go:1`** — resolved~~',
            '- ~~**[M3]** **`fix.py:5`** — no longer applies~~',
        ],
        ids=["strikethrough-S", "strikethrough-M"],
    )
    def test_strikethrough_format(self, line):
        m = review_findings.FINDING_ID_RE.match(line)
        assert m is not None, f"FINDING_ID_RE did not match strikethrough: {line!r}"

    def test_extracts_severity_and_seq(self):
        line = '- **[N7]** **`foo.py:1`** — trailing whitespace'
        m = review_findings.FINDING_ID_RE.match(line)
        assert m is not None
        assert m.group(1) == "N"
        assert m.group(2) == "7"

    def test_agent_example_format_from_reviewer_md(self):
        reviewer_path = AGENTS_DIR / "reviewer.md"
        if not reviewer_path.exists():
            pytest.skip("agents/reviewer.md not found")

        content = reviewer_path.read_text()
        example_re = re.compile(r"^- \*\*\[([MSNI])\d+\]\*\*", re.MULTILINE)
        examples = example_re.findall(content)
        assert examples, "No example finding lines found in reviewer.md"

        # Find full lines matching the pattern
        example_lines = [
            line for line in content.split("\n")
            if example_re.match(line.strip())
        ]
        for line in example_lines:
            m = review_findings.FINDING_ID_RE.match(line.strip())
            assert m is not None, (
                f"FINDING_ID_RE does not match reviewer.md example: {line.strip()!r}"
            )


# ── 4. TestTemplateVariableCoverage ──────────────────────────────────────────


def _collect_string_literals_in_function(func_source: str) -> set[str]:
    """Extract all string literal keys that appear in the function's source."""
    # Match dict literal keys: "key": or 'key':
    keys = set(re.findall(r'["\'](\w+)["\']\s*:', func_source))
    # Also match bare string assignments like kwarg references
    keys |= set(re.findall(r'["\'](\w+)["\']', func_source))
    return keys


def _get_prompt_handler_sources() -> dict[str, str]:
    """Map template name -> source code of its _prompt_* handler function."""
    source = (LIB_DIR / "review_prompt.py").read_text()
    tree = ast.parse(source)
    source_lines = source.split("\n")

    # Find _PROMPT_HANDLERS dict to map template constants to function names
    handler_map: dict[str, str] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_PROMPT_HANDLERS":
                    if isinstance(node.value, ast.Dict):
                        for key, val in zip(node.value.keys, node.value.values):
                            if isinstance(key, ast.Name) and isinstance(val, ast.Name):
                                handler_map[key.id] = val.id

    # Resolve constant names to their string values
    constants = _collect_template_constants()
    resolved_map: dict[str, str] = {}
    for const_name, func_name in handler_map.items():
        template_value = constants.get(const_name)
        if template_value:
            resolved_map[template_value] = func_name

    # Extract source code of each handler function
    handler_sources: dict[str, str] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in resolved_map.values():
                start = node.lineno - 1
                end = node.end_lineno
                func_src = "\n".join(source_lines[start:end])
                # Find which template this function handles
                for tpl, fn in resolved_map.items():
                    if fn == node.name:
                        handler_sources[tpl] = func_src
    return handler_sources


def _get_common_kwargs_keys() -> set[str]:
    """Extract the keys from the `common` dict built in build_prompt()."""
    source = (LIB_DIR / "review_prompt.py").read_text()
    tree = ast.parse(source)

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "build_prompt":
                source_lines = source.split("\n")
                start = node.lineno - 1
                end = node.end_lineno
                func_src = "\n".join(source_lines[start:end])
                return _collect_string_literals_in_function(func_src)
    return set()


class TestTemplateVariableCoverage:
    """Template ${var} placeholders have corresponding kwargs in prompt handlers."""

    @pytest.fixture(scope="class")
    def handler_sources(self):
        return _get_prompt_handler_sources()

    @pytest.fixture(scope="class")
    def common_keys(self):
        return _get_common_kwargs_keys()

    @pytest.mark.parametrize("template_name", sorted(_template_files()))
    def test_template_vars_referenced_in_handler(
        self, template_name, handler_sources, common_keys,
    ):
        template_path = TEMPLATE_DIR / template_name
        template_vars = _extract_template_vars(template_path)
        if not template_vars:
            pytest.skip(f"No ${{var}} placeholders in {template_name}")

        handler_src = handler_sources.get(template_name)
        if handler_src is None:
            pytest.skip(f"No _prompt_* handler found for {template_name}")

        handler_strings = _collect_string_literals_in_function(handler_src)
        all_available = handler_strings | common_keys

        missing = sorted(template_vars - all_available)
        # safe_substitute won't error on missing vars, but they indicate drift
        assert not missing, (
            f"Template {template_name} uses ${{var}} placeholders not found "
            f"as string literals in the handler or common dict:\n"
            + "\n".join(f"  - ${{{v}}}" for v in missing)
        )


# ── 5. Python script --help smoke tests ──────────────────────────────────────


_PYTHON_SCRIPTS = _python_scripts_with_shebang()


@pytest.mark.parametrize(
    "script",
    _PYTHON_SCRIPTS,
    ids=[s.name for s in _PYTHON_SCRIPTS],
)
def test_python_script_help_exits_zero(script):
    """Python CLI scripts must exit 0 on --help."""
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"{script.name} --help failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout[:500]}\n"
        f"stderr: {result.stderr[:500]}"
    )
