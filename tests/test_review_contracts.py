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
LIB_DIR = REPO_ROOT / "ai" / "lib"
TEMPLATE_DIR = LIB_DIR / "review-templates"
BIN_DIR = REPO_ROOT / "ai" / "claude" / "bin"
AGENTS_DIR = REPO_ROOT / "ai" / "claude" / "agents"

# Insert lib dir so we can import review_common / review_findings directly
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import make_ctx  # noqa: E402

import fix_engine  # noqa: E402
import fix_tracking  # noqa: E402
import review_common  # noqa: E402
import review_findings  # noqa: E402
import review_fix  # noqa: E402
import review_prompt  # noqa: E402
import review_types  # noqa: E402
from pr_state import PRIdentity, PRState  # noqa: E402
from review_preflight import MAX_PROMPT_BYTES  # noqa: E402
from review_types import (  # noqa: E402
    PRContext, PreflightData, PRMetadata, ReviewJob,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _extract_template_assign(node: ast.Assign) -> tuple[str, str] | None:
    """If node is a TEMPLATE_* = "..." assignment, return (name, value)."""
    for target in node.targets:
        if not isinstance(target, ast.Name):
            continue
        if not target.id.startswith("TEMPLATE_"):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return (target.id, node.value.value)
    return None


def _collect_template_constants() -> dict[str, str]:
    """Return {constant_name: value} for all TEMPLATE_* constants in review_common."""
    tree = ast.parse((LIB_DIR / "review_common.py").read_text())
    constants: dict[str, str] = {}
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        pair = _extract_template_assign(node)
        if pair:
            constants[pair[0]] = pair[1]
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


# ── 1b. TestReviewMeta ───────────────────────────────────────────────────────


class TestReviewMeta:
    """review_meta_from_dict handles edge cases correctly."""

    def test_empty_string_pr_number_returns_none(self):
        """Empty-string pr_number from meta.json must not crash with ValueError on int("")."""
        meta = review_types.review_meta_from_dict({"pr_number": ""})
        assert meta.pr_number is None

    def test_valid_pr_number_as_string(self):
        meta = review_types.review_meta_from_dict({"pr_number": "42"})
        assert meta.pr_number == 42

    def test_missing_pr_number_returns_none(self):
        meta = review_types.review_meta_from_dict({})
        assert meta.pr_number is None

    def test_none_pr_number_returns_none(self):
        meta = review_types.review_meta_from_dict({"pr_number": None})
        assert meta.pr_number is None

    def test_timestamps_are_absent_when_the_file_predates_them(self):
        """No backfill: a meta.json without them reports them as absent."""
        meta = review_types.review_meta_from_dict({})
        assert meta.started_at == ""
        assert meta.reviewed_at == ""

    def test_timestamps_are_read_from_the_file(self):
        meta = review_types.review_meta_from_dict({
            "started_at": "2026-08-18T13:47:03+00:00",
            "reviewed_at": "2026-08-18T14:02:11+00:00",
        })
        assert meta.started_at == "2026-08-18T13:47:03+00:00"
        assert meta.reviewed_at == "2026-08-18T14:02:11+00:00"


# ── 2. TestSeverityConsistency ───────────────────────────────────────────────


class TestSeverityConsistency:
    """Severity registry is internally consistent."""

    def test_every_severity_key_is_single_char(self):
        for s in review_types.SEVERITIES:
            assert len(s.key) == 1, f"{s.key} is not a single character"

    def test_posting_values_are_valid(self):
        for s in review_types.SEVERITIES:
            assert s.posting in ("inline", "body"), f"{s.key} has invalid posting: {s.posting}"

    def test_body_group_values_are_valid(self):
        for s in review_types.SEVERITIES:
            assert s.body_group in ("by_severity", "by_file"), f"{s.key} has invalid body_group: {s.body_group}"

    def test_finding_id_regex_accepts_all_severity_keys(self):
        keys = [s.key for s in review_types.SEVERITIES]
        regex_keys = review_findings.FINDING_ID_RE.pattern
        for key in keys:
            assert key in regex_keys, f"FINDING_ID_RE does not include severity key {key}"


# ── 2b. TestSeverityRegistry ─────────────────────────────────────────────────


class TestSeverityRegistry:
    """SeverityConfig registry provides all severity metadata."""

    def test_severities_has_four_entries(self):
        assert len(review_types.SEVERITIES) == 4

    def test_severity_keys_are_unique(self):
        keys = [s.key for s in review_types.SEVERITIES]
        assert len(keys) == len(set(keys))

    def test_severity_keys_are_msni(self):
        keys = [s.key for s in review_types.SEVERITIES]
        assert keys == ["M", "S", "N", "I"]

    def test_severity_by_key_returns_correct_config(self):
        m = review_types.severity_by_key("M")
        assert m.label == "must-fix"
        assert m.section == "Must fix"
        assert m.posting == "inline"
        assert m.body_group == "by_severity"

    def test_severity_by_key_unknown_raises(self):
        with pytest.raises(KeyError):
            review_types.severity_by_key("X")

    def test_nit_is_body_posting(self):
        n = review_types.severity_by_key("N")
        assert n.posting == "body"
        assert n.body_group == "by_file"

    def test_idiom_is_body_posting(self):
        i = review_types.severity_by_key("I")
        assert i.posting == "body"
        assert i.body_group == "by_file"

    def test_nit_aliases_include_nits(self):
        n = review_types.severity_by_key("N")
        assert "Nits" in n.aliases

    def test_severity_config_is_frozen(self):
        m = review_types.severity_by_key("M")
        with pytest.raises(AttributeError):
            m.key = "X"


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
        assert m.group(2) == severity
        assert int(m.group(3)) == seq

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
        assert m.group(2) == "N"
        assert m.group(3) == "7"

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


# ── 4. Template rendering contracts ──────────────────────────────────────────
#
# These render every template the way production does — through build_prompt or
# through the script's own render function — rather than scraping handler source
# for string literals. A placeholder that never gets a value survives
# safe_substitute as a literal `${var}`, so rendering is the only way to see it.


def _make_review_job(**overrides) -> ReviewJob:
    """A ReviewJob with every optional section populated."""
    preflight = PreflightData(
        diff=(
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        ),
        commit_log="abc1234 feat: stuff",
        file_contents={"a.py": "print(1)\n"},
        file_permissions={"a.py": "100644"},
        claude_md="# Project",
        architecture_md="# Architecture",
        omitted_files=["vendor/x.go"],
        delta_diff="diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
        delta_commit_log="abc1234 feat: stuff",
        delta_files=["a.py"],
        prior_head_sha="0000000",
    )
    pr = PRMetadata(
        title="feat: thing", body="Body", head="user/feat/thing", base="main",
        head_sha="abc1234", additions=10, deletions=5, changed_files=1,
        files=[{"path": "a.py", "additions": 10, "deletions": 5}],
    )
    defaults = dict(
        repo="owner/repo", pr_number="1", pr=pr,
        ctx=PRContext(commits="abc1234 feat: stuff"),
        wt_path="/tmp/wt", review_file="/tmp/reviews/review.md",
        session_log="/tmp/reviews/session.jsonl",
        issue_link="#42", issue_context="Issue body",
        generator_version="test", preflight=preflight,
        viewer_role="OWNER",
    )
    defaults.update(overrides)
    return ReviewJob(**defaults)


# Extra kwargs each handler-backed template needs, keyed by template filename.
_BUILD_PROMPT_EXTRAS = {
    review_common.TEMPLATE_SINGLE: {},
    review_common.TEMPLATE_SELF_REVIEW: {"branch_name": "user/feat/thing"},
    review_common.TEMPLATE_HOLISTIC: {"holistic_output": "/tmp/reviews/holistic.md"},
    review_common.TEMPLATE_SCOUT: {"scout_output": "/tmp/reviews/scout.md"},
    review_common.TEMPLATE_DISPROVE: {
        "disprove_output": "/tmp/reviews/disprove.md",
        "review_content": "- [M1] something",
    },
    review_common.TEMPLATE_GROUP: {
        "group_idx": 1, "group_count": 2, "group_name": "core",
        "group_files_formatted": "- a.py",
        "group_file_paths": ["a.py"],
        "group_output": "/tmp/reviews/group-1.md",
    },
    review_common.TEMPLATE_SYNTHESIS: {
        "group_count": 2, "merged_content": "## Must fix\n",
    },
    review_common.TEMPLATE_SELF_SYNTHESIS: {
        "group_count": 2, "merged_content": "## Must fix\n",
        "branch_name": "user/feat/thing",
    },
}


def _render_via_build_prompt(template_name: str, job: ReviewJob | None = None) -> str:
    return review_prompt.build_prompt(
        template_name, job or _make_review_job(), max_turns=15,
        **_BUILD_PROMPT_EXTRAS[template_name],
    )


def _render_adapter(adapter) -> str:
    """Render a fix template the way `fix_engine` renders it for a real pass.

    Going through the engine rather than restating the substitution keeps this
    honest about what an agent is actually handed: a placeholder the engine
    stopped supplying would show up here as unsubstituted, not as a passing
    test against a call nobody makes.
    """
    adapter.tracking_path.parent.mkdir(parents=True, exist_ok=True)
    adapter.tracking_path.write_text("- [ ] fixed\n")
    return fix_engine._prompt(adapter, 15)


def _render_fix_ci(cc, wt_path) -> str:
    ctx = make_ctx(repo="owner/repo", branch="user/feat/thing",
                   worktree_root=wt_path, target_dir=wt_path)
    return _render_adapter(cc.CIFixAdapter(
        [{"id": "build-1", "job": "build", "kind": "build",
          "annotation": "test failed", "headline": "test failed"}],
        {"run_number": 1}, ctx,
        PRState(identity=PRIdentity(
            repo="owner/repo", branch="user/feat/thing", pr_number=42,
            head_sha="abc123", worktree_root=str(wt_path),
        )),
    ))


def _render_fix_comments(rt, wt_path) -> str:
    ctx = make_ctx(repo="owner/repo", branch="user/feat/thing",
                   pr_number=1, worktree_root=wt_path, target_dir=wt_path)
    adapter = rt.CommentFixAdapter(
        rt.PRReport(repo="owner/repo", pr_number=1), ctx, wt_path,
        fixable=[], fixable_items=[], needs_human=[], dismissed=[],
        already_addressed=[], resolved=[], triage_replies=0,
        has_unaccounted=False, has_items=False,
    )
    # The default-branch checkout is a fetch and a reset against a second
    # worktree; a render has no business making either.
    adapter.__dict__["main_wt"] = None
    return _render_adapter(adapter)


def _render_fix_findings(wt_path) -> str:
    job = _make_review_job(
        wt_path=str(wt_path),
        review_file=str(wt_path / "reviews" / "review.md"),
    )
    finding = review_types.Finding(
        id="M1", severity=review_types.SEVERITY_MUST, seq=1,
        path="a.py", line=3, end_line=None, body="the guard is missing",
    )
    return _render_adapter(review_fix.ReviewFixAdapter(job, [finding], set()))


# Every fix template, keyed the way the parametrized contracts below name them.
# One list, so a fourth domain adopting the engine is added to the contracts by
# adding its renderer here rather than to each test in turn.
_FIX_RENDERERS = {
    "ci": lambda cc, rt, wt: _render_fix_ci(cc, wt),
    "comments": lambda cc, rt, wt: _render_fix_comments(rt, wt),
    "findings": lambda cc, rt, wt: _render_fix_findings(wt),
}


def _make_common_sections() -> review_prompt.CommonSections:
    return review_prompt.CommonSections(
        **{name: "" for name in review_prompt.COMMON_SECTION_NAMES},
    )


def _unsubstituted(rendered: str) -> list[str]:
    return sorted(set(re.findall(r"\$\{(\w+)\}", rendered)))


class TestTemplateRendering:
    """Every template renders with all placeholders substituted."""

    @pytest.mark.parametrize("template_name", sorted(_BUILD_PROMPT_EXTRAS))
    def test_build_prompt_templates_fully_substituted(self, template_name):
        rendered = _render_via_build_prompt(template_name)
        left = _unsubstituted(rendered)
        assert not left, (
            f"{template_name} rendered with unsubstituted placeholders: "
            + ", ".join(f"${{{v}}}" for v in left)
        )

    def test_fix_ci_template_fully_substituted(self, cc, tmp_path):
        left = _unsubstituted(_render_fix_ci(cc, tmp_path))
        assert not left, f"fix-ci.md left: {left}"

    def test_fix_comments_template_fully_substituted(self, rt, tmp_path):
        left = _unsubstituted(_render_fix_comments(rt, tmp_path))
        assert not left, f"fix-comments.md left: {left}"

    def test_fix_findings_template_fully_substituted(self, tmp_path):
        left = _unsubstituted(_render_fix_findings(tmp_path))
        assert not left, f"fix-findings.md left: {left}"

    def test_every_template_is_covered(self):
        """A new template must be added to this file's render coverage."""
        covered = set(_BUILD_PROMPT_EXTRAS) | {
            review_common.TEMPLATE_FIX,
            review_common.TEMPLATE_FIX_CI, review_common.TEMPLATE_FIX_COMMENTS,
        }
        uncovered = sorted(
            name for name in _template_files() - covered
            if _extract_template_vars(TEMPLATE_DIR / name)
        )
        assert not uncovered, (
            "Templates with ${var} placeholders but no render coverage: "
            + ", ".join(uncovered)
        )


class TestOutputBlockContract:
    """Agents run under `claude --bare`, which has no Write tool."""

    # Phrasings that would tell an agent to use the unavailable Write tool.
    _WRITE_MANDATE = re.compile(
        r"(use|using|with|be)\s+the\s+Write\s+tool|Write\s+tool\s+to\s+(create|write|save)",
        re.IGNORECASE,
    )

    @pytest.mark.parametrize("template_name", sorted(_BUILD_PROMPT_EXTRAS))
    def test_no_write_tool_mandate(self, template_name):
        self._assert_no_mandate(
            template_name, _render_via_build_prompt(template_name),
        )

    @pytest.mark.parametrize("render", sorted(_FIX_RENDERERS))
    def test_fix_templates_have_no_write_tool_mandate(self, render, cc, rt, tmp_path):
        self._assert_no_mandate(render, _FIX_RENDERERS[render](cc, rt, tmp_path))

    def _assert_no_mandate(self, label, rendered):
        match = self._WRITE_MANDATE.search(rendered)
        assert not match, (
            f"{label} tells the agent to use the Write tool "
            f"({match.group(0)!r}) — it does not exist under --bare"
        )

    # (template, output path, stdout_warning) — mirrors each handler's
    # b.output(...) call, so the assertion compares against the block the
    # builder actually produces rather than a restated copy of its wording.
    _OUTPUT_BLOCKS = [
        (review_common.TEMPLATE_HOLISTIC, "/tmp/reviews/holistic.md", False),
        (review_common.TEMPLATE_SCOUT, "/tmp/reviews/scout.md", False),
        (review_common.TEMPLATE_DISPROVE, "/tmp/reviews/disprove.md", False),
        (review_common.TEMPLATE_GROUP, "/tmp/reviews/group-1.md", False),
        (review_common.TEMPLATE_SYNTHESIS, "/tmp/reviews/review.md", False),
        (review_common.TEMPLATE_SELF_SYNTHESIS, "/tmp/reviews/review.md", False),
        (review_common.TEMPLATE_SINGLE, "/tmp/reviews/review.md", True),
        (review_common.TEMPLATE_SELF_REVIEW, "/tmp/reviews/review.md", True),
    ]

    @pytest.mark.parametrize(
        "template_name, output_path, stdout_warning", _OUTPUT_BLOCKS,
        ids=[t for t, _, _ in _OUTPUT_BLOCKS],
    )
    def test_output_block_rendered_verbatim(
        self, template_name, output_path, stdout_warning,
    ):
        rendered = _render_via_build_prompt(template_name)
        expected = review_common.build_output_block(
            output_path, stdout_warning=stdout_warning,
        )
        assert expected in rendered

    def test_every_output_writing_template_is_checked(self):
        checked = {t for t, _, _ in self._OUTPUT_BLOCKS}
        expected = {
            name for name in _BUILD_PROMPT_EXTRAS
            if "output_block" in _extract_template_vars(TEMPLATE_DIR / name)
        }
        assert expected == checked

    @pytest.mark.parametrize("render", sorted(_FIX_RENDERERS))
    def test_fix_templates_share_the_worktree_block(self, render, cc, rt, tmp_path):
        rendered = _FIX_RENDERERS[render](cc, rt, tmp_path)
        assert review_common.build_worktree_block(str(tmp_path)) in rendered

    @pytest.mark.parametrize("render", sorted(_FIX_RENDERERS))
    def test_fix_templates_explain_every_box_the_checklist_offers(
        self, render, cc, rt, tmp_path,
    ):
        """The boxes are `fix_tracking`'s; the prose explaining them is per-domain.

        Every template spells out the same three answers in its own words, so a
        box renamed or added in `fix_tracking` leaves prose behind that describes
        a checklist the agent is not looking at. No template has to say it the
        same way — each only has to still be talking about all of them.
        """
        task = _FIX_RENDERERS[render](cc, rt, tmp_path).split("## Task", 1)[1]
        for box in fix_tracking._BOXES:
            why = (
                f" — {fix_tracking._WHY}"
                if box.outcome in fix_tracking._REASONED
                else ""
            )
            assert f"`- [x] {box.label}{why}`" in task, box.label


class TestSharedSectionNames:
    """`shared()` takes bare strings — catch typos statically, not at call time."""

    def _shared_call_args(self) -> list[tuple[int, str]]:
        tree = ast.parse((LIB_DIR / "review_prompt.py").read_text())
        return [
            (node.lineno, arg.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "shared"
            for arg in node.args
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        ]

    def test_handlers_only_share_real_fields(self):
        calls = self._shared_call_args()
        assert calls, "found no shared() calls to check — did the API change?"
        bad = [
            f"review_prompt.py:{lineno} {name!r}"
            for lineno, name in calls
            if name not in review_prompt.COMMON_SECTION_NAMES
        ]
        assert not bad, "shared() called with non-CommonSections names: " + ", ".join(bad)

    def test_unknown_name_raises_with_the_valid_set(self):
        builder = review_prompt.PromptBuilder(_make_common_sections())
        with pytest.raises(KeyError, match="pr_haeder"):
            builder.shared("pr_haeder")

    def test_every_common_field_is_reachable(self):
        """A field no handler shares is dead weight on every prompt build."""
        shared_names = {name for _, name in self._shared_call_args()}
        unused = sorted(review_prompt.COMMON_SECTION_NAMES - shared_names)
        assert not unused, f"CommonSections fields no handler uses: {unused}"


class TestPromptBudgetAccounting:
    """Sections registered on the builder count against the diff budget.

    The group handler once computed its budget before registering
    `project_context` and never counted `group_files_formatted` at all, so those
    bytes landed on top of a diff already sized to fill the budget.
    """

    # Enough per-file diffs to overflow the budget, so truncation is granular
    # and the prompt sits right at the limit rather than dropping one big blob.
    _PATHS = [f"f{i:03d}.py" for i in range(60)]

    def _group_prompt(self, files_formatted: str) -> str:
        job = _make_review_job()
        job.pr.files = [
            {"path": p, "additions": 1, "deletions": 0} for p in self._PATHS
        ]
        job.preflight.diff = "".join(
            f"diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n"
            + "+xxxxxxxxxxxxxxxxxxxx\n" * 500
            for p in self._PATHS
        )
        job.preflight.file_contents = {}
        extra = dict(_BUILD_PROMPT_EXTRAS[review_common.TEMPLATE_GROUP])
        extra["group_file_paths"] = list(self._PATHS)
        extra["group_files_formatted"] = files_formatted
        return review_prompt.build_prompt(
            review_common.TEMPLATE_GROUP, job, max_turns=15, **extra,
        )

    def test_large_section_shrinks_the_diff_instead_of_the_prompt_budget(self):
        filler = "- filler.py\n" * 2000
        small = self._group_prompt("- a.py")
        large = self._group_prompt("- a.py\n" + filler)

        assert len(small.encode()) <= MAX_PROMPT_BYTES
        assert len(large.encode()) <= MAX_PROMPT_BYTES
        # The filler must come out of the diff's share, not on top of it.
        growth = len(large.encode()) - len(small.encode())
        assert growth < len(filler.encode()) // 2, (
            f"adding {len(filler)}B to group_files_formatted grew the prompt by "
            f"{growth}B — the section is not counted against the diff budget"
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
