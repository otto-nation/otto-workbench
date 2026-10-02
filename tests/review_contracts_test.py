"""Cross-file contract tests for the review system.

Verifies that constants, templates, regex patterns, and CLI interfaces
stay consistent across agent.registry, agent.templates, review.document,
review.prompt, review-templates/, and agents/reviewer.md.

All expectations are derived dynamically from source — no hardcoded lists.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
TEMPLATE_DIR = LIB_DIR / "review-templates"
BIN_DIR = REPO_ROOT / "ai" / "bin"
AGENTS_DIR = REPO_ROOT / "ai" / "claude" / "agents"

# Insert lib dir so we can import the review modules directly
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.registry  # noqa: E402
import agent.templates  # noqa: E402
from agent.registry import PHASES, REVIEW_PHASES  # noqa: E402
import core.serde  # noqa: E402
from core.phases import Mode, Phase  # noqa: E402
import review.grammar  # noqa: E402
import review.prompt  # noqa: E402
import review.spans  # noqa: E402
import review.types  # noqa: E402

from review_contracts_support import _template_files


# ── Helpers ──────────────────────────────────────────────────────────────────


def _declared_templates() -> dict[Phase, set[str]]:
    """Return {phase: every template it renders} for the phases declaring one.

    Asked through `template_for` for each mode rather than off the field, so a
    mode-keyed spec that names a template for one mode and not the other raises
    here rather than at the review that reaches for the missing one.
    """
    return {
        phase: {spec.template_for(mode) for mode in Mode}
        for phase, spec in PHASES.items() if spec.template
    }


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
    """Every template a phase declares maps to a file and vice versa."""

    def test_every_declared_template_has_a_file(self):
        declared = _declared_templates()
        assert declared, "No phase in the registry declares a template"

        files = _template_files()
        missing = {
            phase: sorted(names - files)
            for phase, names in declared.items() if names - files
        }
        assert not missing, (
            "Phases declare templates that do not exist:\n"
            + "\n".join(f"  - {p}: {n}" for p, n in sorted(missing.items()))
        )

    def test_every_template_file_is_declared(self):
        declared = set().union(*_declared_templates().values())
        files = _template_files()
        assert files, "No .md files found in review-templates/"

        unreferenced = sorted(files - declared)
        assert not unreferenced, (
            "Template files no phase declares:\n"
            + "\n".join(f"  - {f}" for f in unreferenced)
        )


# ── 1b. TestReviewMeta ───────────────────────────────────────────────────────


class TestReviewMeta:
    """review_meta_from_dict handles edge cases correctly."""

    def test_empty_string_pr_number_returns_none(self):
        """Empty-string pr_number from meta.json must not crash with ValueError on int("")."""
        meta = review.types.review_meta_from_dict({"pr_number": ""})
        assert meta.pr_number is None

    def test_valid_pr_number_as_string(self):
        meta = review.types.review_meta_from_dict({"pr_number": "42"})
        assert meta.pr_number == 42

    def test_missing_pr_number_returns_none(self):
        meta = review.types.review_meta_from_dict({})
        assert meta.pr_number is None

    def test_none_pr_number_returns_none(self):
        meta = review.types.review_meta_from_dict({"pr_number": None})
        assert meta.pr_number is None

    def test_timestamps_are_absent_when_the_file_predates_them(self):
        """No backfill: a meta.json without them reports them as absent."""
        meta = review.types.review_meta_from_dict({})
        assert meta.started_at == ""
        assert meta.reviewed_at == ""

    def test_timestamps_are_read_from_the_file(self):
        meta = review.types.review_meta_from_dict({
            "started_at": "2026-08-18T13:47:03+00:00",
            "reviewed_at": "2026-08-18T14:02:11+00:00",
        })
        assert meta.started_at == "2026-08-18T13:47:03+00:00"
        assert meta.reviewed_at == "2026-08-18T14:02:11+00:00"

    def test_the_host_is_read_from_the_file(self):
        meta = review.types.review_meta_from_dict({"host": "ghe.acme.com"})
        assert meta.host == "ghe.acme.com"

    # passes-at-base: pins the back-compat read this change was careful not to break
    def test_a_sidecar_predating_the_host_still_names_its_repo(self):
        """The field is additive: an older meta.json loses nothing by lacking it.

        `serde` drops a value whose shape does not match its hint, so a field
        added in the wrong shape would take the whole record's `repo` down with
        it and silently unattribute every review already on disk.
        """
        meta = review.types.review_meta_from_dict(
            {"repo": "acme/widget", "pr_number": "7", "head_sha": "abc123"})
        assert meta.host == ""
        assert meta.repo == "acme/widget"
        assert meta.pr_number == 7

    def test_the_host_survives_a_write_and_read(self):
        """What `review-post` depends on: the host reaches it through the file."""
        written = core.serde.to_dict(
            review.types.ReviewMeta(repo="acme/widget", host="ghe.acme.com"))
        assert written["host"] == "ghe.acme.com"
        assert core.serde.from_dict(review.types.ReviewMeta, written).host == "ghe.acme.com"


# ── 1c. TestPhaseSkipFlags ───────────────────────────────────────────────────


def _skip_flag_parser():
    parser = argparse.ArgumentParser()
    agent.registry.add_phase_skip_flags(parser)
    return parser


class TestPhaseSkipFlags:
    """`--no-<phase>` is generated, so the two CLIs cannot drift from each other.

    `review` offers the flags, `review-orchestrate` parses them, and the
    first forwards them to the second on argv. All three read the registry.
    """

    def test_a_flag_per_optional_review_phase(self):
        offered = {
            dest for dest in vars(_skip_flag_parser().parse_args([]))
            if dest.startswith("no_")
        }
        assert offered == {
            f"no_{p}" for p in REVIEW_PHASES if PHASES[p].optional
        }

    def test_nothing_skipped_by_default(self):
        args = _skip_flag_parser().parse_args([])
        assert agent.registry.phase_skips(args) == frozenset()

    def test_each_flag_names_its_own_phase(self):
        for phase in (p for p in REVIEW_PHASES if PHASES[p].optional):
            args = _skip_flag_parser().parse_args([f"--no-{phase}"])
            assert agent.registry.phase_skips(args) == frozenset({phase})

    def test_argv_round_trips_through_the_parser(self):
        skips = frozenset({Phase.GROUP, Phase.SYNTHESIS, Phase.DISPROVE})
        argv = agent.registry.phase_skip_argv(skips)
        assert agent.registry.phase_skips(_skip_flag_parser().parse_args(argv)) == skips

    def test_argv_follows_the_registry_order(self):
        every = frozenset(p for p in REVIEW_PHASES if PHASES[p].optional)
        assert agent.registry.phase_skip_argv(every) == [
            f"--no-{p}" for p in REVIEW_PHASES if PHASES[p].optional
        ]

    def test_no_flag_for_a_required_phase(self):
        with pytest.raises(SystemExit):
            _skip_flag_parser().parse_args([f"--no-{Phase.SINGLE}"])

    def test_both_clis_offer_the_same_flags(self):
        generated = sorted(
            f"--no-{p}" for p in REVIEW_PHASES if PHASES[p].optional
        )
        for script in ("review", "review-orchestrate"):
            helped = subprocess.run(
                [str(REPO_ROOT / "ai" / "bin" / script), "--help"],
                capture_output=True, text=True, timeout=60,
            ).stdout
            for flag in generated:
                assert flag in helped, f"{script} does not offer {flag}"


# ── 2. TestSeverityConsistency ───────────────────────────────────────────────


class TestSeverityConsistency:
    """Severity registry is internally consistent."""

    def test_every_severity_key_is_single_char(self):
        for s in review.types.SEVERITIES:
            assert len(s.key) == 1, f"{s.key} is not a single character"

    def test_posting_values_are_valid(self):
        for s in review.types.SEVERITIES:
            assert s.posting in ("inline", "body"), f"{s.key} has invalid posting: {s.posting}"

    def test_body_group_values_are_valid(self):
        for s in review.types.SEVERITIES:
            assert s.body_group in ("by_severity", "by_file"), f"{s.key} has invalid body_group: {s.body_group}"

    def test_finding_id_regex_accepts_all_severity_keys(self):
        keys = [s.key for s in review.types.SEVERITIES]
        regex_keys = review.grammar.FINDING_ID_RE.pattern
        for key in keys:
            assert key in regex_keys, f"FINDING_ID_RE does not include severity key {key}"


# ── 2b. TestSeverityRegistry ─────────────────────────────────────────────────


class TestSeverityRegistry:
    """SeverityConfig registry provides all severity metadata."""

    def test_severities_has_four_entries(self):
        assert len(review.types.SEVERITIES) == 4

    def test_severity_keys_are_unique(self):
        keys = [s.key for s in review.types.SEVERITIES]
        assert len(keys) == len(set(keys))

    def test_severity_keys_are_msni(self):
        keys = [s.key for s in review.types.SEVERITIES]
        assert keys == ["M", "S", "N", "I"]

    def test_severity_by_key_returns_correct_config(self):
        m = review.types.severity_by_key("M")
        assert m.label == "must-fix"
        assert m.section == "Must fix"
        assert m.posting == "inline"
        assert m.body_group == "by_severity"

    def test_severity_by_key_unknown_raises(self):
        with pytest.raises(KeyError):
            review.types.severity_by_key("X")

    def test_nit_is_body_posting(self):
        n = review.types.severity_by_key("N")
        assert n.posting == "body"
        assert n.body_group == "by_file"

    def test_idiom_is_body_posting(self):
        i = review.types.severity_by_key("I")
        assert i.posting == "body"
        assert i.body_group == "by_file"

    def test_nit_aliases_include_nits(self):
        n = review.types.severity_by_key("N")
        assert "Nits" in n.aliases

    def test_severity_config_is_frozen(self):
        m = review.types.severity_by_key("M")
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
        m = review.grammar.FINDING_ID_RE.match(line)
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
        m = review.grammar.FINDING_ID_RE.match(line)
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
        m = review.grammar.FINDING_ID_RE.match(line)
        assert m is not None, f"FINDING_ID_RE did not match strikethrough: {line!r}"

    def test_extracts_severity_and_seq(self):
        line = '- **[N7]** **`foo.py:1`** — trailing whitespace'
        m = review.grammar.FINDING_ID_RE.match(line)
        assert m is not None
        assert m.group(2) == "N"
        assert m.group(3) == "7"

    def test_finding_span_boundary_agrees_with_the_id_regex(self):
        """Every line that opens a finding also ends the one above it.

        A head pattern that recognised a declaration the boundary did not would
        let one span open inside another, which is how a finding came to own the
        evidence written under its neighbour.
        """
        for line in [
            '- **[M1]** **`handler.go:42`** — description',
            '- [ ] **[S2]** **`api.go:10`** — missing context',
            '- [x] **[N3]** `README.md:1` — typo',
            '- ~~**[I4]** **`old.go:1`** — resolved~~',
        ]:
            assert review.grammar.FINDING_ID_RE.match(line)
            assert review.spans.ends_finding_body(line), (
                f"opens a finding but does not end the one above it: {line!r}"
            )

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
            m = review.grammar.FINDING_ID_RE.match(line.strip())
            assert m is not None, (
                f"FINDING_ID_RE does not match reviewer.md example: {line.strip()!r}"
            )


_EXAMPLE_BLOCK_RE = re.compile(r"^```[a-z]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
_EXAMPLE_FINDING_RE = re.compile(
    r"^- (?:\[[ x]\] )?(?:~~)?\*\*\[[MSNI]\d+\]", re.MULTILINE,
)


def _example_reviews() -> list[tuple[str, str]]:
    """Every fenced block in the specs that writes out a review, named by file.

    Scraped rather than listed, so a spec that starts demonstrating a new shape
    under a finding is checked without this file being edited.
    """
    sources = [AGENTS_DIR / "reviewer.md", *sorted(TEMPLATE_DIR.glob("*.md"))]
    return [
        (path.name, block)
        for path in sources if path.exists()
        for block in _EXAMPLE_BLOCK_RE.findall(path.read_text())
        if _EXAMPLE_FINDING_RE.search(block)
    ]


def _documented_body_lines(block: str) -> list[str]:
    """Every line the example writes under a finding declaration.

    A heading or a blank line closes the run: what follows either is the spec
    talking about something other than the finding above it.
    """
    body: list[str] = []
    under_finding = False
    for raw in block.split("\n"):
        stripped = raw.strip()
        if _EXAMPLE_FINDING_RE.match(stripped):
            under_finding = True
        elif not stripped or stripped.startswith("#"):
            under_finding = False
        elif under_finding:
            body.append(stripped)
    return body


class TestSpecBodyShapesBelongToTheFindingAboveThem:
    """What the specs write under a finding, `finding_spans` reads as its body.

    The agents write reviews to these examples, so a continuation shape one of
    them demonstrates that `ends_finding_body` treats as a boundary is a body
    the pipeline would cut in half — the finding loses its evidence and an
    orphan fragment is left where the finding used to end.
    """

    def test_the_specs_demonstrate_body_shapes_at_all(self):
        assert any(_documented_body_lines(block) for _, block in _example_reviews())

    @pytest.mark.parametrize(
        "source,block", _example_reviews(), ids=lambda v: v if len(v) < 24 else "block",
    )
    def test_no_documented_body_line_ends_the_finding(self, source, block):
        for line in _documented_body_lines(block):
            assert not review.spans.ends_finding_body(line), (
                f"{source} writes this under a finding, "
                f"but ends_finding_body reads it as a boundary: {line!r}"
            )

    @pytest.mark.parametrize(
        "source,block", _example_reviews(), ids=lambda v: v if len(v) < 24 else "block",
    )
    def test_every_documented_body_line_lands_in_a_span(self, source, block):
        claimed: set[str] = set()
        for span in review.spans.finding_spans(block):
            claimed.update(
                line.strip() for line in span.text_of(block).split("\n") if line.strip()
            )
        for line in _documented_body_lines(block):
            assert line in claimed, (
                f"{source} writes this under a finding, "
                f"but no finding span claims it: {line!r}"
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
