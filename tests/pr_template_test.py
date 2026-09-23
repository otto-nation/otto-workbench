"""Tests for the one resolver of a repo's PR template.

Three callers used to answer this question separately — `task pr:create` in
bash, `pr describe` in Python, and now the SessionStart line. What each of them
does with the answer is tested with that caller; what the answer *is* is tested
here.
"""

import subprocess
import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

from core import pr_template  # noqa: E402

CLI = LIB_DIR / "core" / "pr_template.py"


def _write(root: Path, rel: str, text: str) -> None:
    """Create a template at *rel*, making its directory."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# ── where a template may live ───────────────────────────────────────────────


@pytest.mark.parametrize("rel", pr_template.TEMPLATE_PATHS)
def test_every_documented_location_is_found(tmp_path, rel):
    """Each location GitHub honours, resolved on its own.

    Parametrized over the constant rather than over a list written here: a
    location added to the tuple without being resolvable fails this, and a
    hand-written list would just be the same drift one directory over.

    The path is compared case-insensitively because the filesystem decides how
    many files these six names are. macOS is case-insensitive, so writing the
    uppercase spelling creates the file the lowercase candidate finds first and
    the resolved path is the other spelling of the one written; Linux, where CI
    runs, has two distinct files and returns exactly what was written. Asserting
    equality would pass on one platform and fail on the other for a resolution
    that is correct on both — and the case of the name is not what any caller
    reads. GitHub's filenames are case-insensitive too, so agreeing with the
    filesystem here is agreeing with GitHub.
    """
    _write(tmp_path, rel, "## Why\n")
    template = pr_template.load(tmp_path)
    assert template.found
    assert template.text == "## Why\n"
    assert template.path.casefold() == rel.casefold()


def test_the_docs_directory_is_a_location(tmp_path):
    """The gap that motivated one owner, pinned on its own.

    Both lists this module replaced stopped at the repo root and `.github/`, so
    a repo keeping its template in `docs/` — which GitHub has always honoured —
    was told it shipped none and had the fallback's headers pushed at it. A
    parametrized case above covers this too; it is named here so the regression
    is legible as itself rather than as one row of six.
    """
    _write(tmp_path, "docs/pull_request_template.md", "## Why\n")
    template = pr_template.load(tmp_path)
    assert template.found
    assert template.path == "docs/pull_request_template.md"


def test_earlier_locations_win(tmp_path):
    _write(tmp_path, ".github/pull_request_template.md", "first")
    _write(tmp_path, "PULL_REQUEST_TEMPLATE.md", "middle")
    _write(tmp_path, "docs/pull_request_template.md", "last")
    assert pr_template.load(tmp_path).text == "first"


def test_a_template_in_a_subdirectory_is_not_found(tmp_path):
    """Only repo-root-relative locations count, which is GitHub's own rule.

    A `packages/api/.github/` template is not the repo's template, and treating
    it as one would have `pr:create` enforce sections GitHub never shows.
    """
    _write(tmp_path, "packages/api/.github/pull_request_template.md", "## Why\n")
    assert not pr_template.load(tmp_path).found


# ── a repo with no template ─────────────────────────────────────────────────


def test_a_repo_without_one_gets_the_fallback(tmp_path):
    template = pr_template.load(tmp_path)
    assert not template.found
    assert template.path == ""
    assert template.text == pr_template.FALLBACK_TEMPLATE


def test_an_unreadable_template_reads_as_absent(tmp_path):
    """A permissions problem degrades like a missing file rather than raising.

    Both callers are better off with the fallback than with a traceback: one is
    generating a PR body and the other is a context line.
    """
    path = tmp_path / ".github" / "pull_request_template.md"
    path.parent.mkdir()
    path.write_text("## Why\n")
    path.chmod(0o000)
    try:
        assert not pr_template.load(tmp_path).found
    finally:
        # Restored so tmp_path's cleanup can remove it.
        path.chmod(0o644)


# ── the headers a body owes ─────────────────────────────────────────────────


def test_headers_are_the_sections_a_body_must_carry(tmp_path):
    _write(tmp_path, ".github/pull_request_template.md",
           "## What\n\nsome prose\n\n## Why\n")
    assert pr_template.load(tmp_path).headers == ["## What", "## Why"]


def test_nested_headers_are_not_sections(tmp_path):
    """`###` is structure inside a section, and a body is never asked for one."""
    _write(tmp_path, ".github/pull_request_template.md",
           "## What\n\n### Details\n\n## Why\n")
    assert pr_template.load(tmp_path).headers == ["## What", "## Why"]


def test_a_template_with_no_headers_requires_no_sections(tmp_path):
    _write(tmp_path, ".github/pull_request_template.md", "just prose\n")
    template = pr_template.load(tmp_path)
    assert template.found
    assert template.headers == []


# ── the record bash reads ───────────────────────────────────────────────────


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True, text=True, timeout=10,
    )


def test_the_record_is_the_path_then_the_template(tmp_path):
    _write(tmp_path, ".github/pull_request_template.md", "## What\n\n## Why\n")
    result = _cli("--root", str(tmp_path))
    assert result.returncode == 0
    path, _, text = result.stdout.partition("\n")
    assert path == ".github/pull_request_template.md"
    assert text == "## What\n\n## Why\n"


def test_a_repo_with_no_template_records_an_empty_path(tmp_path):
    result = _cli("--root", str(tmp_path))
    assert result.returncode == 0
    path, _, text = result.stdout.partition("\n")
    assert path == ""
    assert text == pr_template.FALLBACK_TEMPLATE


def test_an_empty_template_yields_a_record_with_no_body(tmp_path):
    """The case a naive split reads as the path being the template.

    `${record#*$'\\n'}` returns the whole record when there is no newline, so
    bash would take the path line as the body. The shell caller tests its own
    handling; this pins the record shape it is handling.
    """
    _write(tmp_path, ".github/pull_request_template.md", "")
    result = _cli("--root", str(tmp_path))
    assert result.stdout == ".github/pull_request_template.md\n"


def test_the_fallback_can_be_asked_for_alone(tmp_path):
    """What `generate-git-rules` renders into the rules.

    Asked for outright rather than resolved against an empty directory: that
    spelling needs a temp directory and a second stage to strip the path line,
    and a shell pipeline's exit status would be the stripper's.
    """
    result = _cli("--fallback")
    assert result.returncode == 0
    assert result.stdout == pr_template.FALLBACK_TEMPLATE + "\n"


def test_a_root_that_is_not_a_directory_is_an_error(tmp_path):
    """A caller bug, distinguished from a repo that ships no template.

    Both would otherwise print an empty path and exit 0, so a mistyped root
    would read as the fallback and the PR body would be built against it.
    """
    result = _cli("--root", str(tmp_path / "nope"))
    assert result.returncode == 1
    assert "not a directory" in result.stderr


# ── the rules render this fallback ──────────────────────────────────────────


def test_the_generated_rules_carry_the_fallback_template():
    """`git.generated.md` renders the constant rather than a copy of it.

    The rules tell an agent which sections to write when a repo ships none, and
    `task pr:create` fills those same sections on that same repo. A second copy
    in the generator is the two disagreeing about what a templateless PR looks
    like.
    """
    rules = (Path(__file__).resolve().parent.parent
             / "ai" / "guidelines" / "rules" / "git.generated.md").read_text()
    assert pr_template.FALLBACK_TEMPLATE in rules
