"""Which source `pr.ci_annotations` believes about a failed job, and what it makes of it."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr import ci_annotations  # noqa: E402
from pr import ci_failures as ci  # noqa: E402


def _no_log_fallback(kind):
    """A `log_fallback` result for a job whose logs yielded nothing."""
    return ci_annotations.LogFallback([], "", kind, structured=False)


# ── fetch_job_failure ───────────────────────────────────────────────────


def test_fetch_job_failure_returns_correct_structure():
    """fetch_job_failure returns a JobFailure carrying the job's classified items."""
    annotations = [
        {"annotation_level": "failure", "message": "SC2086: Double quote", "path": "bin/foo.sh", "start_line": 42},
    ]
    job = {"name": "shellcheck", "conclusion": "failure", "databaseId": 10}
    run_data = {"databaseId": 100}
    with patch("gh.run_reads.fetch_annotations", return_value=annotations):
        result = ci_annotations.fetch_job_failure("owner/repo", job, run_data)
    assert result is not None
    assert result.job_name == "shellcheck"
    assert result.kind == ci.FailureKind.LINT
    assert len(result.items) == 1
    assert result.items[0].file == "bin/foo.sh"
    assert result.failed_step is None


def test_fetch_job_failure_with_no_annotations():
    """fetch_job_failure falls back when no annotations exist."""
    job = {"name": "Build", "conclusion": "failure", "databaseId": 10}
    run_data = {"databaseId": 100}
    with patch("gh.run_reads.fetch_annotations", return_value=[]):
        with patch("pr.ci_annotations.log_fallback", return_value=_no_log_fallback(ci.FailureKind.BUILD)):
            result = ci_annotations.fetch_job_failure("owner/repo", job, run_data)
    assert result is not None
    assert result.job_name == "Build"
    assert "no-annotation" in result.items[0].id


# ── extract_failed_step ─────────────────────────────────────────────────


def test_extract_failed_step_from_steps():
    job = {
        "name": "Generate & verify",
        "steps": [
            {"name": "Checkout", "conclusion": "success"},
            {"name": "Setup Node", "conclusion": "success"},
            {"name": "Generate & check drift", "conclusion": "failure"},
            {"name": "Post Checkout", "conclusion": "skipped"},
        ],
    }
    assert ci_annotations.extract_failed_step(job) == "Generate & check drift"


def test_extract_failed_step_no_steps():
    job = {"name": "Build"}
    assert ci_annotations.extract_failed_step(job) is None


def test_extract_failed_step_all_success():
    job = {
        "name": "Lint",
        "steps": [
            {"name": "Checkout", "conclusion": "success"},
            {"name": "Run lint", "conclusion": "success"},
        ],
    }
    assert ci_annotations.extract_failed_step(job) is None


def test_extract_failed_step_timed_out():
    job = {
        "name": "Slow tests",
        "steps": [
            {"name": "Run tests", "conclusion": "timed_out"},
        ],
    }
    assert ci_annotations.extract_failed_step(job) == "Run tests"


# ── annotations_uninformative ──────────────────────────────────────────


def test_uninformative_no_paths():
    """Annotations without file paths are uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1", "path": ""},
    ]
    assert ci_annotations.annotations_uninformative(annotations) is True


def test_uninformative_with_path():
    """Annotations with file paths are informative."""
    annotations = [
        {"annotation_level": "failure", "message": "SC2086: Double quote", "path": "bin/foo.sh", "start_line": 42},
    ]
    assert ci_annotations.annotations_uninformative(annotations) is False


def test_uninformative_ignores_notices():
    """Notice-level annotations are ignored when checking informativeness."""
    annotations = [
        {"annotation_level": "notice", "message": "some notice", "path": "README.md"},
        {"annotation_level": "failure", "message": "Process completed with exit code 1", "path": ""},
    ]
    assert ci_annotations.annotations_uninformative(annotations) is True


def test_uninformative_mixed_informative_and_not():
    """If any non-notice annotation has a path, annotations are informative."""
    annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1", "path": ""},
        {"annotation_level": "failure", "message": "error TS2304: Cannot find name 'foo'", "path": "src/app.ts", "start_line": 10},
    ]
    assert ci_annotations.annotations_uninformative(annotations) is False


def test_uninformative_generic_path_dot_github():
    """Annotations with path='.github' and generic message are uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1.", "path": ".github", "start_line": 405},
    ]
    assert ci_annotations.annotations_uninformative(annotations) is True


def test_uninformative_generic_exit_code_message():
    """Annotations with a source path but generic 'exit code' message are uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1.", "path": "src/main.go", "start_line": 1},
    ]
    assert ci_annotations.annotations_uninformative(annotations) is True


def test_uninformative_exited_with_code():
    """'exited with code' variant is also uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Step exited with code 1", "path": "src/main.go", "start_line": 1},
    ]
    assert ci_annotations.annotations_uninformative(annotations) is True


def test_uninformative_returned_non_zero():
    """'returned a non-zero code' variant is also uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Command returned a non-zero code: 2", "path": "Makefile", "start_line": 10},
    ]
    assert ci_annotations.annotations_uninformative(annotations) is True


def test_uninformative_check_failure_on_line():
    """'check failure on line' variant is also uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Check failure on line 42", "path": ".github/workflows/ci.yml", "start_line": 42},
    ]
    assert ci_annotations.annotations_uninformative(annotations) is True


def test_uninformative_failed_with_exit_code():
    """'failed with exit code' variant is also uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Job failed with exit code 1", "path": ".github/workflows/ci.yml", "start_line": 1},
    ]
    assert ci_annotations.annotations_uninformative(annotations) is True


def test_informative_real_error_with_source_path():
    """Annotations with a real source path and specific error are informative."""
    annotations = [
        {"annotation_level": "failure", "message": "SC2086: Double quote to prevent globbing", "path": "bin/foo.sh", "start_line": 42},
    ]
    assert ci_annotations.annotations_uninformative(annotations) is False


def test_informative_mixed_generic_and_specific():
    """If any annotation has a real path and specific message, annotations are informative."""
    annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1.", "path": ".github", "start_line": 405},
        {"annotation_level": "failure", "message": "error TS2304: Cannot find name 'foo'", "path": "src/app.ts", "start_line": 10},
    ]
    assert ci_annotations.annotations_uninformative(annotations) is False


# ── parse_test_artifact ─────────────────────────────────────────────────


def test_parse_test_artifact_jsonl(tmp_path):
    """Artifact with Go test JSONL should extract failure output."""
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    jsonl = artifact_dir / "test-results.json"
    lines = [
        '{"Action":"output","Package":"github.com/foo/tests","Output":"=== RUN   TestFoo\\n"}',
        '{"Action":"output","Package":"github.com/foo/tests","Output":"    foo_test.go:42: expected 1, got 2\\n"}',
        '{"Action":"output","Package":"github.com/foo/tests","Output":"--- FAIL: TestFoo (0.01s)\\n"}',
        '{"Action":"fail","Package":"github.com/foo/tests","Elapsed":0.01}',
    ]
    jsonl.write_text("\n".join(lines))

    result = ci_annotations.parse_test_artifact(str(artifact_dir))
    assert "--- FAIL: TestFoo" in result
    assert "expected 1, got 2" in result


def test_parse_test_artifact_returns_empty_on_no_failures(tmp_path):
    """Artifact with all passing tests returns empty."""
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    jsonl = artifact_dir / "test-results.json"
    lines = [
        '{"Action":"output","Package":"github.com/foo/tests","Output":"=== RUN   TestFoo\\n"}',
        '{"Action":"pass","Package":"github.com/foo/tests","Elapsed":0.01}',
    ]
    jsonl.write_text("\n".join(lines))

    result = ci_annotations.parse_test_artifact(str(artifact_dir))
    assert result == ""


def test_parse_test_artifact_keeps_the_records_a_truncated_artifact_did_finish(tmp_path):
    """A job killed mid-upload leaves a half-written line; the rest is still the failure."""
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    lines = [
        '{"Action":"output","Package":"github.com/foo/tests","Output":"--- FAIL: TestFoo (0.01s)\\n"}',
        '{"Action":"fail","Package":"github.com/foo/tests","Elapsed":0.01}',
        '{"Action":"output","Package":"github.com/foo/te',
    ]
    (artifact_dir / "test-results.json").write_text("\n".join(lines))

    assert "--- FAIL: TestFoo" in ci_annotations.parse_test_artifact(str(artifact_dir))


# ── fetch_test_artifact ─────────────────────────────────────────────────


def test_fetch_test_artifact_asks_for_the_artifact_the_job_uploads():
    """A `Test: svc-payment` job uploads `test-results-svc-payment`."""
    with patch("gh.run_reads.download_artifact") as mock_download:
        mock_download.return_value.__enter__.return_value = None
        ci_annotations.fetch_test_artifact("owner/repo", 100, "Test: svc-payment")
    assert mock_download.call_args.args[2] == "test-results-svc-payment"


def test_fetch_test_artifact_is_empty_when_the_job_uploaded_none():
    with patch("gh.run_reads.download_artifact") as mock_download:
        mock_download.return_value.__enter__.return_value = None
        assert ci_annotations.fetch_test_artifact("owner/repo", 100, "Test: svc-payment") == ""


# ── failure ids and the text behind them ───────────────────────────────


def _id_under_hash_seed(message: str, seed: str) -> str:
    """`build_failure_id` for a pathless, untitled annotation, in its own process."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]);"
         "from pr import ci_annotations;"
         "print(ci_annotations.build_failure_id({'message': sys.argv[2]}, 'Build'))",
         str(LIB_DIR), message],
        capture_output=True, text=True, check=True,
        env={**os.environ, "PYTHONHASHSEED": seed},
    )
    return result.stdout.strip()


def test_a_pathless_untitled_failure_keeps_one_id_across_processes():
    """Progression matches a failure against the prior run by id, so an id built
    from the builtin `hash` — whose string seed is fresh per process — reports
    every such failure as new. Two seeds have to produce the one id."""
    assert (_id_under_hash_seed("segfault in worker", "1")
            == _id_under_hash_seed("segfault in worker", "2"))


def test_an_annotation_with_an_empty_message_reports_its_title():
    """`build_failure_id` already reads an empty message as no message; the text
    the item carries reads it the same way rather than surfacing blank."""
    annotations = [
        {"annotation_level": "failure", "message": "", "title": "shellcheck SC2086"},
    ]

    items = ci_annotations.annotations_to_items(annotations, "Lint")

    assert items[0].annotation == "shellcheck SC2086"


# ── annotations_to_items headline from context ─────────────────────────


def test_annotations_to_items_headline_from_context():
    """When annotation text has no headline, derive it from context."""
    annotations = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1.", "path": ".github", "start_line": 405},
    ]
    context = "--- FAIL: TestFoo (0.01s)\n    foo_test.go:42: expected 1, got 2"
    items = ci_annotations.annotations_to_items(annotations, "Test: svc-payment", source_run_id=100, context=context)
    assert len(items) == 1
    assert "FAIL: TestFoo" in items[0].headline


# ── fetch_job_failure artifact fallback ─────────────────────────────────


_UNINFORMATIVE_ANNOTATIONS = [
    {"annotation_level": "failure", "message": "Process completed with exit code 1.", "path": ".github", "start_line": 405},
]

_ARTIFACT_CONTEXT = "--- FAIL: TestFoo (0.01s)\n    foo_test.go:42: expected 1, got 2"


@patch("pr.ci_annotations.fetch_test_artifact", return_value=_ARTIFACT_CONTEXT)
@patch("pr.ci_annotations.log_fallback", return_value=_no_log_fallback(ci.FailureKind.TEST))
@patch("gh.run_reads.fetch_annotations", return_value=_UNINFORMATIVE_ANNOTATIONS)
def test_fetch_job_failure_uses_artifact_fallback(_mock_ann, _mock_log, _mock_art):
    """When annotations are uninformative and logs are empty, artifact fallback triggers."""
    job = {"name": "Test: svc-payment", "conclusion": "failure", "databaseId": 10,
           "_source_run_id": 100}
    result = ci_annotations.fetch_job_failure("owner/repo", job, {"databaseId": 100})
    assert result is not None
    assert result.items[0].context == _ARTIFACT_CONTEXT
    assert "FAIL: TestFoo" in result.items[0].headline


@patch("pr.ci_annotations.fetch_test_artifact")
@patch("pr.ci_annotations.log_fallback")
@patch("gh.run_reads.fetch_annotations", return_value=_UNINFORMATIVE_ANNOTATIONS)
def test_fetch_job_failure_skips_artifact_when_logs_succeed(_mock_ann, mock_log, mock_artifact):
    """When log fallback produces context, artifact download is not attempted."""
    log_context = "--- FAIL: TestBar (0.02s)\n    bar_test.go:10: wrong result"
    log_annotations = [{"message": log_context, "path": "", "start_line": 0, "title": ""}]
    mock_log.return_value = ci_annotations.LogFallback(
        log_annotations, log_context, ci.FailureKind.TEST, structured=False,
    )
    job = {"name": "Test: svc-payment", "conclusion": "failure", "databaseId": 10}
    ci_annotations.fetch_job_failure("owner/repo", job, {"databaseId": 100})
    mock_artifact.assert_not_called()


@patch("pr.ci_annotations.fetch_test_artifact")
@patch("pr.ci_annotations.log_fallback")
@patch("gh.run_reads.fetch_annotations")
def test_fetch_job_failure_no_artifact_for_lint(mock_ann, mock_log, mock_artifact):
    """LINT failures do not trigger artifact download even when uninformative."""
    mock_ann.return_value = [
        {"annotation_level": "failure", "message": "Process completed with exit code 1.", "path": "", "start_line": 0},
    ]
    job = {"name": "shellcheck", "conclusion": "failure", "databaseId": 10}
    ci_annotations.fetch_job_failure("owner/repo", job, {"databaseId": 100})
    mock_log.assert_not_called()
    mock_artifact.assert_not_called()


# ── Test failures ──────────────────────────────────────────────────────────

# Trimmed from run #2893: the failure near the top, and at the bottom the
# warning traces and runner exit message the old extraction anchored on.
_BATS_LOG = "\n".join([
    "2026-08-28T18:34:33.5541835Z not ok 1648 bats_skip survives a setup in 71ms",
    "2026-08-28T18:34:33.5542700Z # (in test file tests/ui_facade.bats, line 164)",
    "2026-08-28T18:34:33.5543479Z #   `[[ \"$output\" == *\"# skip\"* ]]' failed",
    "2026-08-28T18:34:33.5544239Z ok 1651 output.sh accepts current bash in 27ms",
    "2026-08-28T18:35:03.5727631Z        in test file tests/install_targeted.bats, line 150)",
    "2026-08-28T18:35:03.7583872Z ##[error]Process completed with exit code 1.",
])

_BATS_JOB = {"name": "Tests (bats)", "conclusion": "failure", "databaseId": 10,
             "_source_run_id": 100}


def _bats_items():
    """`fetch_job_failure` for a bats job whose only annotation is generic."""
    with patch("gh.run_reads.fetch_annotations", return_value=_UNINFORMATIVE_ANNOTATIONS), \
         patch("gh.run_reads.fetch_job_logs", return_value=_BATS_LOG):
        return ci_annotations.fetch_job_failure("owner/repo", _BATS_JOB, {"databaseId": 100}).items


def test_bats_failure_reports_the_failing_assertion():
    item = _bats_items()[0]
    assert (item.file, item.line) == ("tests/ui_facade.bats", 164)


def test_bats_failure_does_not_inherit_the_generic_annotation_location():
    """The generic annotation anchors on `.github` — a log location beats it."""
    item = _bats_items()[0]
    assert item.file != _UNINFORMATIVE_ANNOTATIONS[0]["path"]
    assert item.line != _UNINFORMATIVE_ANNOTATIONS[0]["start_line"]


def test_bats_failure_headline_names_the_failing_test():
    assert "bats_skip survives a setup" in _bats_items()[0].headline


def test_bats_failure_id_is_stable_across_runs():
    assert _bats_items()[0].id == _bats_items()[0].id == "err-tests/ui_facade.bats-164"


def test_every_bats_failure_gets_its_own_item():
    log = "\n".join([
        "2026-08-28T18:34:33.5541835Z not ok 2 failing early in 3ms",
        "2026-08-28T18:34:33.5542700Z # (in test file tests/a.bats, line 10)",
        "2026-08-28T18:34:33.5544239Z ok 3 passing later in 1ms",
        "2026-08-28T18:34:33.5545000Z not ok 4 failing late in 1ms",
        "2026-08-28T18:34:33.5546000Z # (in test file tests/b.bats, line 18)",
    ])
    with patch("gh.run_reads.fetch_annotations", return_value=_UNINFORMATIVE_ANNOTATIONS), \
         patch("gh.run_reads.fetch_job_logs", return_value=log):
        result = ci_annotations.fetch_job_failure("owner/repo", _BATS_JOB, {"databaseId": 100})
    assert [(i.file, i.line) for i in result.items] == [
        ("tests/a.bats", 10), ("tests/b.bats", 18),
    ]


def test_an_unlocated_tap_failure_keys_on_its_test_name():
    """`hash()` is randomised per process, so a message hash never matches twice."""
    log = "2026-08-28T18:34:33.5541835Z not ok 1 setup_file failed in 2ms"
    with patch("gh.run_reads.fetch_annotations", return_value=_UNINFORMATIVE_ANNOTATIONS), \
         patch("gh.run_reads.fetch_job_logs", return_value=log):
        result = ci_annotations.fetch_job_failure("owner/repo", _BATS_JOB, {"databaseId": 100})
    assert result.items[0].id == "Tests (bats)-setup-file-failed"


# Trimmed from run 32793239084: one failure raised inside a helper, so pytest
# prints the caller's frame at 954 above the `_ _ _ _` rule and the raise site
# at 937 below it, then the summary and the same generic runner message.
_PYTEST_LOG = "\n".join([
    "2026-08-25T00:21:37.7671403Z _____ TestRetry.test_retries_on_zero_progress _____",
    "2026-08-25T00:21:37.7679183Z >       job = self._make_job(tmp_path)",
    "2026-08-25T00:21:37.7679478Z tests/test_review_fix_pass.py:954:",
    "2026-08-25T00:21:37.7679799Z _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _",
    "2026-08-25T00:21:37.7705587Z E       NameError: name '_git' is not defined",
    "2026-08-25T00:21:37.7706200Z tests/test_review_fix_pass.py:937: NameError",
    "2026-08-25T00:21:37.7819142Z =========================== short test summary info ============================",
    "2026-08-25T00:21:37.7822162Z FAILED tests/test_review_fix_pass.py::TestRetry::test_retries_on_zero_progress - NameError",
    "2026-08-25T00:21:37.8604173Z ##[error]Process completed with exit code 1.",
])

_PYTEST_JOB = {"name": "Tests (pytest)", "conclusion": "failure", "databaseId": 11,
               "_source_run_id": 100}


def _pytest_items(log=_PYTEST_LOG):
    """`fetch_job_failure` for a pytest job whose only annotation is generic."""
    with patch("gh.run_reads.fetch_annotations", return_value=_UNINFORMATIVE_ANNOTATIONS), \
         patch("gh.run_reads.fetch_job_logs", return_value=log):
        return ci_annotations.fetch_job_failure("owner/repo", _PYTEST_JOB, {"databaseId": 100}).items


def test_pytest_failure_reports_the_frame_that_raised():
    item = _pytest_items()[0]
    assert (item.file, item.line) == ("tests/test_review_fix_pass.py", 937)


def test_pytest_failure_does_not_inherit_the_generic_annotation_location():
    item = _pytest_items()[0]
    assert item.file != _UNINFORMATIVE_ANNOTATIONS[0]["path"]
    assert item.line != _UNINFORMATIVE_ANNOTATIONS[0]["start_line"]


def test_pytest_failure_id_is_stable_across_runs():
    assert _pytest_items()[0].id == _pytest_items()[0].id == "err-tests/test_review_fix_pass.py-937"


def test_pytest_failure_headline_names_the_failing_test():
    assert _pytest_items()[0].headline.startswith(
        "FAILED tests/test_review_fix_pass.py::TestRetry::test_retries_on_zero_progress")


def test_an_unlocated_pytest_failure_keys_on_its_test_name():
    log = "\n".join([
        "2026-08-25T00:21:37.7819142Z ====================== short test summary info =======================",
        "2026-08-25T00:21:37.7822162Z FAILED tests/a_test.py::TestRetry::test_collected - RuntimeError",
    ])
    assert _pytest_items(log)[0].id == "Tests (pytest)-testretry-test-collected"


def test_a_non_tap_log_still_keeps_the_original_annotations():
    """Only a location the log reported displaces GitHub's own annotation."""
    log = "2026-06-22T17:22:01Z --- FAIL: TestFoo (0.01s)\n2026-06-22T17:22:01Z FAIL"
    job = {"name": "Test: svc-payment", "conclusion": "failure", "databaseId": 10}
    with patch("gh.run_reads.fetch_annotations", return_value=_UNINFORMATIVE_ANNOTATIONS), \
         patch("gh.run_reads.fetch_job_logs", return_value=log):
        result = ci_annotations.fetch_job_failure("owner/repo", job, {"databaseId": 100})
    assert result.items[0].file == _UNINFORMATIVE_ANNOTATIONS[0]["path"]
    assert "FAIL: TestFoo" in result.items[0].context
