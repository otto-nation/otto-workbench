"""pr CLI: `pr review` and `pr comments` — auto-self, mode flags, --post, --repair,
--summary and --list."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# `reviews_dir` is not imported — pytest discovers conftest fixtures itself,
# and importing one shadows the fixture with a plain function.
from conftest import make_ctx, seed_review

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402

import cli.registry  # noqa: E402
import cli.schema  # noqa: E402
import review.listing

from pr_cli_support import _run_main, _lock_file, _dispatch_stage


# ── cmd_review auto-self ──────────────────────────────────────────────────


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_review_injects_self_when_no_target(mock_call):
    mock_call.return_value = 0
    ctx = make_ctx(pr_number=None)
    cli.pr.cmd_review([], ctx, bin_dir=BIN_DIR)
    cmd = mock_call.call_args[0][1]
    assert "--self" in cmd


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_review_no_self_when_pr_number(mock_call):
    mock_call.return_value = 0
    ctx = make_ctx()
    cli.pr.cmd_review(["123"], ctx, bin_dir=BIN_DIR)
    cmd = mock_call.call_args[0][1]
    self_count = cmd.count("--self")
    assert self_count == 0


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_review_no_self_when_pr_url(mock_call):
    mock_call.return_value = 0
    ctx = make_ctx()
    cli.pr.cmd_review(["https://github.com/owner/repo/pull/99"], ctx, bin_dir=BIN_DIR)
    cmd = mock_call.call_args[0][1]
    assert "--self" not in cmd


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_review_no_self_when_original_pr(mock_call):
    """--pr consumed by global parser still prevents --self injection."""
    mock_call.return_value = 0
    ctx = make_ctx()
    cli.pr.cmd_review([], ctx, original_pr="1206", bin_dir=BIN_DIR)
    cmd = mock_call.call_args[0][1]
    assert "--self" not in cmd


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_review_no_self_when_ctx_has_pr(mock_call):
    """Auto-detected PR number in context prevents --self injection."""
    mock_call.return_value = 0
    ctx = make_ctx(pr_number=99)
    cli.pr.cmd_review([], ctx, bin_dir=BIN_DIR)
    cmd = mock_call.call_args[0][1]
    assert "--self" not in cmd


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_review_no_double_self(mock_call):
    mock_call.return_value = 0
    ctx = make_ctx()
    cli.pr.cmd_review(["--self"], ctx, bin_dir=BIN_DIR)
    cmd = mock_call.call_args[0][1]
    assert cmd.count("--self") == 1


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_review_no_self_when_branch_positional(mock_call):
    """A branch name positional should not trigger --self injection."""
    mock_call.return_value = 0
    ctx = make_ctx(pr_number=None)
    cli.pr.cmd_review(["kgn/go-update"], ctx, bin_dir=BIN_DIR)
    cmd = mock_call.call_args[0][1]
    assert "--self" not in cmd
    assert "kgn/go-update" in cmd


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_review_passes_flags_through(mock_call):
    mock_call.return_value = 0
    ctx = make_ctx()
    cli.pr.cmd_review(["--self", "--fix", "--no-post"], ctx, bin_dir=BIN_DIR)
    cmd = mock_call.call_args[0][1]
    assert "--fix" in cmd
    assert "--no-post" in cmd


# ── cmd_review --recover mode flag ───────────────────────────────────────


def test_review_recover_mutually_exclusive_with_post():
    """--recover and --post are mutually exclusive."""
    ctx = make_ctx()
    rc = cli.pr.cmd_review(["--recover", "--post"], ctx, bin_dir=BIN_DIR)
    assert rc == 1


def test_review_recover_passes_through_to_delegate():
    """--recover alone is forwarded to review."""
    ctx = make_ctx()
    with patch("core.publishing.call_entry_point", return_value=0) as mock_call:
        cli.pr.cmd_review(["--recover", "42"], ctx, bin_dir=BIN_DIR)
    assert mock_call.call_args[0][0] == "cli.review_entry:main"
    cmd = mock_call.call_args[0][1]
    assert "--recover" in cmd


# ── cmd_review --post ────────────────────────────────────────────────────


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_review_post_delegates_to_review_post(mock_call, reviews_dir):
    mock_call.return_value = 0
    review_dir = reviews_dir / "repo-42"
    review_dir.mkdir()
    (review_dir / "review.md").write_text("# Review")
    rc = cli.pr.cmd_review(["--post"], make_ctx(pr_number=42), bin_dir=BIN_DIR)
    assert rc == 0
    cmd = mock_call.call_args[0][1]
    assert mock_call.call_args[0][0] == "cli.review_post:main"
    assert "--pr" in cmd
    assert "--review-file" in cmd


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_review_post_passes_submit(mock_call, reviews_dir):
    mock_call.return_value = 0
    review_dir = reviews_dir / "repo-42"
    review_dir.mkdir()
    (review_dir / "review.md").write_text("# Review")
    rc = cli.pr.cmd_review(["--post", "--submit"], make_ctx(pr_number=42), bin_dir=BIN_DIR)
    assert rc == 0
    cmd = mock_call.call_args[0][1]
    assert "--submit" in cmd


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_review_post_names_the_branch_it_publishes_for(mock_call, reviews_dir):
    """The review is found by PR number; the run lock keys on the branch.

    Without the branch travelling with the request, review-post has nothing to
    check the sidecar against and will publish whatever the PR-keyed lookup
    found — including a review a run the lock never made contend with is still
    writing.
    """
    mock_call.return_value = 0
    review_dir = reviews_dir / "repo-42"
    review_dir.mkdir()
    (review_dir / "review.md").write_text("# Review")

    cli.pr.cmd_review(["--post"], make_ctx(pr_number=42, branch="isaac/feat/x"), bin_dir=BIN_DIR)

    cmd = mock_call.call_args[0][1]
    assert "--expect-ref" in cmd
    assert cmd[cmd.index("--expect-ref") + 1] == "isaac/feat/x"


def test_cmd_review_post_fails_without_review_file(reviews_dir):
    rc = cli.pr.cmd_review(["--post"], make_ctx(pr_number=42), bin_dir=BIN_DIR)
    assert rc == 1


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_review_post_finds_review_via_meta(mock_call, reviews_dir):
    """--post discovers a review stored under a non-canonical directory name."""
    mock_call.return_value = 0
    alt_dir = reviews_dir / "repo-self-some-branch"
    alt_dir.mkdir()
    (alt_dir / "review.md").write_text("# Review")
    (alt_dir / "meta.json").write_text(json.dumps({
        "repo": "owner/repo", "pr_number": "42",
    }))
    rc = cli.pr.cmd_review(["--post"], make_ctx(pr_number=42), bin_dir=BIN_DIR)
    assert rc == 0
    cmd = mock_call.call_args[0][1]
    assert mock_call.call_args[0][0] == "cli.review_post:main"
    assert str(alt_dir / "review.md") in cmd


# ── cmd_comments ────────────────────────────────────────────────────────────


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_comments_plain_delegates_to_review_threads(mock_call):
    mock_call.return_value = 0
    ctx = make_ctx()
    cli.pr.cmd_comments([], ctx)
    cmd = mock_call.call_args[0][1]
    assert mock_call.call_args[0][0] == "cli.review_threads:main"
    assert "--triage" not in cmd
    assert "--finish" not in cmd


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_comments_triage_passes_flag(mock_call):
    mock_call.return_value = 0
    ctx = make_ctx()
    cli.pr.cmd_comments(["--triage"], ctx)
    cmd = mock_call.call_args[0][1]
    assert mock_call.call_args[0][0] == "cli.review_threads:main"
    assert "--triage" in cmd


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_comments_finish_passes_flag(mock_call):
    mock_call.return_value = 0
    ctx = make_ctx()
    cli.pr.cmd_comments(["--finish"], ctx)
    cmd = mock_call.call_args[0][1]
    assert mock_call.call_args[0][0] == "cli.review_threads:main"
    assert "--finish" in cmd


# ── cmd_review --repair ────────────────────────────────────────────────────


@patch("cli.review_modes.sync_review_domain")
@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_review_does_not_rewrite_domain_after_delegate(
        mock_call, mock_sync, reviews_dir):
    """review already wrote the domain; pr must not write it again."""
    mock_call.return_value = 0
    rc = cli.pr.cmd_review(["123"], make_ctx(pr_number=42), bin_dir=BIN_DIR)
    assert rc == 0
    mock_sync.assert_not_called()


@patch("cli.review_modes.sync_review_domain")
def test_cmd_review_repair_succeeds_with_review_file(mock_sync, reviews_dir):
    review_dir = reviews_dir / "repo-42"
    review_dir.mkdir()
    (review_dir / "review.md").write_text("## Nit\n- **[N1]** path:1 — style\n")
    rc = cli.pr.cmd_review(["--repair"], make_ctx(pr_number=42), bin_dir=BIN_DIR)
    assert rc == 0
    mock_sync.assert_called_once()


@patch("core.publishing.call_entry_point", return_value=0)
def test_cmd_review_repair_falls_back_to_rebuild(mock_call, reviews_dir):
    (reviews_dir / "repo-42").mkdir()
    mock_call.return_value = 0
    rc = cli.pr.cmd_review(["--repair"], make_ctx(pr_number=42), bin_dir=BIN_DIR)
    assert rc == 0
    assert mock_call.call_args[0][0] == "cli.review_rebuild:main"


def test_cmd_review_repair_no_pr_fails():
    ctx = make_ctx(pr_number=None)
    rc = cli.pr.cmd_review(["--repair"], ctx, bin_dir=BIN_DIR)
    assert rc == 1


# ── cmd_review --summary ───────────────────────────────────────────────────


def test_cmd_review_summary_outputs_json(reviews_dir, capsys):
    review_dir = reviews_dir / "repo-42"
    review_dir.mkdir()
    (review_dir / "review.md").write_text("## Must fix\n- **[M1]** path:1 — bug\n")
    rc = cli.pr.cmd_review(["--summary"], make_ctx(pr_number=42), bin_dir=BIN_DIR)
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("REVIEW_SUMMARY:")
    data = json.loads(out.removeprefix("REVIEW_SUMMARY:"))
    assert data["findings"]["must_fix"] == 1


def test_cmd_review_summary_fails_without_review(reviews_dir):
    rc = cli.pr.cmd_review(["--summary"], make_ctx(pr_number=42), bin_dir=BIN_DIR)
    assert rc == 1


# ── cmd_review mutual exclusivity ─────────────────────────────────────────


def test_cmd_review_mutual_exclusivity():
    ctx = make_ctx()
    rc = cli.pr.cmd_review(["--post", "--repair"], ctx, bin_dir=BIN_DIR)
    assert rc == 1


def test_cmd_review_mutual_exclusivity_three():
    ctx = make_ctx()
    rc = cli.pr.cmd_review(["--post", "--repair", "--summary"], ctx, bin_dir=BIN_DIR)
    assert rc == 1


# ── review --list ───────────────────────────────────────────────────────────


def _a_review(reviews_dir, **meta):
    """An attributed review, which is what a listing test is usually about."""
    return seed_review(reviews_dir, name="repo-42", repo="acme/widget", **meta)


def test_review_list_resolves_no_context_at_all():
    """The listing reads the state root, so resolving one would be a `gh` call
    and a target directory spent on values the handler never reads."""
    stage = _dispatch_stage("review", "--list", ctx=make_ctx())
    assert not stage.remote.called
    assert not stage.local.called
    assert not stage.update.called


def test_review_list_takes_no_lock(tmp_path):
    """Nothing here writes, and the lock belongs to a target this has none of."""
    target = tmp_path / "target"
    _dispatch_stage("review", "--list", ctx=make_ctx(target_dir=target))
    assert not _lock_file(target).exists()


def test_review_delegating_still_resolves_updates_and_locks(tmp_path):
    """A mode flag decides `review`'s need; without one nothing changes."""
    target = tmp_path / "target"
    stage = _dispatch_stage("review", ctx=make_ctx(target_dir=target))
    assert stage.remote.called
    assert stage.update.called
    assert _lock_file(target).is_file()


@pytest.mark.parametrize("mode", ["--post", "--repair", "--summary", "--recover"])
def test_a_mode_reaches_dispatch_without_the_worktree_moving(mode, tmp_path):
    """The declaration read end to end: `update_to_remote` is what fetches and
    resets, and no mode may reach its handler having called it."""
    target = tmp_path / "target"
    stage = _dispatch_stage("review", mode, ctx=make_ctx(target_dir=target))
    assert not stage.update.called
    assert stage.remote.called, "the PR still has to be named"
    assert _lock_file(target).is_file(), "and the run still holds the lock"


def test_review_list_refuses_a_positional_target(capsys):
    """`pr review 123 --list` reads as "the reviews for #123". Answering with
    every review on the machine is a wrong answer that looks like a right one,
    so a target a NONE-depth command cannot honour is refused, not dropped."""
    assert _run_main("review", "123", "--list") == cli.pr.EXIT_USAGE
    err = capsys.readouterr().err
    assert "123" in err
    assert "pr review --list" in err, "the mode flag is part of the identity"


def test_review_list_refuses_an_explicit_pr_flag(capsys):
    """The flag spelling reaches the same depth by another road."""
    assert _run_main("review", "--list", "--pr", "123") == cli.pr.EXIT_USAGE
    assert "123" in capsys.readouterr().err


def test_review_list_refuses_an_explicit_branch(capsys):
    assert _run_main("review", "--list", "--branch", "isaac/feat/x") == \
        cli.pr.EXIT_USAGE
    assert "isaac/feat/x" in capsys.readouterr().err


def test_review_list_without_a_target_is_untouched(reviews_dir, capsys):
    """The refusal is about a target that cannot be honoured, not about the
    mode — the ordinary invocation still serves its document."""
    _a_review(reviews_dir, pr_number=42)
    assert _run_main("review", "--list", "--schema-version", "1") in (None, 0)
    assert json.loads(capsys.readouterr().out)["reviews"]


def test_review_list_serves_the_declared_schema_version(reviews_dir, capsys):
    _a_review(reviews_dir, pr_number=42)
    assert _run_main("review", "--list", "--schema-version", "1") in (None, 0)
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema_version"] == 1
    assert [r["repo"] for r in doc["reviews"]] == ["acme/widget"]
    assert doc["reviews"][0]["findings"] == {
        "must_fix": 1, "should_fix": 0, "nit": 0, "idiom": 0, "total": 1,
    }


def test_review_list_does_not_read_the_version_as_a_pr_target(reviews_dir, capsys):
    """`--schema-version 1` is global, so its value never reaches the argv the
    positional scan reads — the arity bug the global flag sidesteps."""
    _a_review(reviews_dir, pr_number=42)
    _run_main("review", "--list", "--schema-version", "1")
    doc = json.loads(capsys.readouterr().out)
    assert doc["reviews"][0]["pr_number"] == 42


def test_review_list_records_no_trail(reviews_dir, capsys):
    """The two records a dispatch writes cost more than the listing itself, and
    they land in the file every `otto-log` query then reads. A query that
    resolves no subject and takes no lock is not an action a trail is for."""
    _a_review(reviews_dir)
    mock_trail = MagicMock()
    with patch("sys.argv", ["pr", "review", "--list", "--schema-version", "1"]), \
         patch("cli.pr.Trail.start", return_value=mock_trail) as mock_start:
        try:
            cli.pr.main(bin_dir=BIN_DIR)
        except SystemExit as e:
            assert e.code in (None, 0)
    assert json.loads(capsys.readouterr().out)["reviews"]
    assert mock_start.call_args.kwargs["record"] is False


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_non_list_dispatch_records_a_trail(mock_resolve, mock_call):
    """The exemption is scoped to `review --list`; every other dispatch still
    tells `Trail.start` to record, which is the wiring the exemption test
    above would miss if `_dispatch` stopped passing it through."""
    mock_resolve.return_value = make_ctx()
    mock_call.return_value = 0
    mock_trail = MagicMock()
    with patch("sys.argv", ["pr", "rebase"]), \
         patch("cli.pr.Trail.start", return_value=mock_trail) as mock_start:
        try:
            cli.pr.main(bin_dir=BIN_DIR)
        except SystemExit:
            pass
    assert mock_start.call_args.kwargs["record"] is True


def test_only_the_listing_is_exempt_from_the_trail():
    """The trail is unconditional apart from the NONE/no-lock queries, and the
    hole is declared by the same three axes as everything else — no command
    carries a trail opt-out of its own for someone to add themselves to.

    `review --list` and `batch` both answer from the state root and take no
    target lock; `batch run` opens its own `pr-batch` trail.
    """
    for name, spec in cli.registry.COMMANDS.items():
        if name == "batch":
            assert not cli.registry.need_for(spec, []).records_a_trail, name
            continue
        assert cli.registry.need_for(spec, []).records_a_trail, name
    listing = cli.registry.need_for(cli.registry.COMMANDS["review"], ["--list"])
    assert not listing.records_a_trail


def test_review_list_carries_no_review_content(reviews_dir, capsys):
    """A consumer polling on an interval would otherwise carry every review's
    full text on every tick."""
    _a_review(reviews_dir)
    _run_main("review", "--list", "--schema-version", "1")
    doc = json.loads(capsys.readouterr().out)
    assert "review_content" not in doc["reviews"][0]
    assert doc["reviews"][0]["review_file"].endswith("/review.md")


def test_review_list_reports_a_missing_root_as_no_reviews(tmp_path, monkeypatch,
                                                          capsys):
    """Absence is not an error — the process is the error channel, and a root
    nothing has written to is a machine that has never run a review."""
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "never-written"))
    assert _run_main("review", "--list", "--schema-version", "1") in (None, 0)
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1, "reviews": [],
    }


def test_review_list_without_a_handshake_writes_nothing_to_stdout(reviews_dir,
                                                                  capsys):
    """A consumer that forgets `--schema-version` gets a parse failure from an
    empty stream rather than a human table it half-understands."""
    _a_review(reviews_dir)
    assert _run_main("review", "--list") in (None, 0)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "acme/widget" in captured.err


def test_an_unsupported_schema_version_is_refused(reviews_dir, capsys):
    """The supported set is allowed to shrink — that is the whole enforcement
    mechanism, so a version this build dropped has to fail loudly."""
    unsupported = str(max(review.listing.SCHEMA_VERSIONS) + 1)
    assert _run_main("review", "--list", "--schema-version", unsupported) == \
        cli.pr.EXIT_USAGE
    err = capsys.readouterr().err
    assert unsupported in err
    assert "1" in err


def test_a_command_serving_no_document_refuses_the_handshake(capsys):
    """`--schema-version` is global so one parse can see it, not because every
    command answers it."""
    assert _run_main("status", "--schema-version", "1") == \
        cli.pr.EXIT_USAGE
    assert "pr review --list" in capsys.readouterr().err


def test_the_schema_contracts_are_read_off_the_mode_table():
    """An error naming an invocation that no longer serves a document is worse
    than no error at all."""
    assert cli.schema.schema_contracts() == ["pr review --list"]
    assert cli.schema.served_schema_versions("review", ["--list"]) == \
        review.listing.SCHEMA_VERSIONS
    assert cli.schema.served_schema_versions("review", ["--summary"]) == ()
    assert cli.schema.served_schema_versions("status", []) == ()
