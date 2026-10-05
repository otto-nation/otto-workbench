"""`pr describe` as the successor to the retired Taskfile updater.

Overrides (`--title`, `--body`/`--body-file`), `--closes`, close-ref
preservation, the PR's own base for the diff range, and the flags the command
line grows. Each behaviour delta is labelled U1-U6 below, in the section
comments ahead of its tests, purely as an internal cross-reference within this
file — the numbering has no meaning outside it.
"""

import sys
from pathlib import Path
from unittest import mock

import pytest

from conftest import make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.backend  # noqa: E402
import cli.pr_describe  # noqa: E402
import config.workbench_config  # noqa: E402
import core.run_lock  # noqa: E402
import gh.client  # noqa: E402
import git.client  # noqa: E402
import git.topology  # noqa: E402
import pr.context  # noqa: E402
import pr.describe  # noqa: E402
import pr.domains  # noqa: E402
import pr.gh_token  # noqa: E402
import pr.state  # noqa: E402
from config.workbench_config import IssueProvider  # noqa: E402
from pr.describe import DescribeOptions  # noqa: E402

TEMPLATE = "## What\n\n## Why\n"

# The real fetch, kept before the fixture below replaces it, for the one test
# that drives it.
_REAL_FETCH = pr.describe._fetch_refusal


@pytest.fixture(autouse=True)
def _fetched(monkeypatch):
    """Every run's `git fetch origin <base>` succeeds; the worktree has no origin."""
    monkeypatch.setattr(pr.describe, "_fetch_refusal", lambda wt, base: "")


def _ctx(worktree, base=""):
    return make_ctx(branch="isaac/feat/x", pr_number=7, worktree_root=worktree,
                    head_sha="aaaa111", target_dir=worktree / "target", base=base)


def _wrapped(body: str) -> str:
    return f"<<<DESCRIPTION>>>\n{body}\n<<<END_DESCRIPTION>>>"


def _described_at_head(worktree):
    """State saying this HEAD was already described and published, so the gate would skip."""
    state = pr.state.new_state("owner/repo", "b", pr_number=7, head_sha="aaaa111",
                               worktree_root=str(worktree))
    pr.state.apply(state, pr.domains.DescribeSummary(head_sha="aaaa111", published=True))
    pr.state.save_state(worktree / "target", state)


def _provider(monkeypatch, provider):
    cfg = mock.MagicMock()
    cfg.issues.provider = provider
    monkeypatch.setattr(config.workbench_config, "load_config", lambda wt: cfg)


class Run:
    """One `run_describe` with gh, git and the model stubbed; records the writes."""

    def __init__(self, worktree, opts=DescribeOptions(), *, body="old body",
                 ai=(_wrapped("NEW BODY"), 0), base=""):
        self.bodies, self.titles, self.git_calls = [], [], []
        with mock.patch.object(pr.describe, "_fetch_pr_body",
                               return_value=("old title", body)), \
             mock.patch.object(pr.describe, "_git",
                               side_effect=lambda wt, *a: self.git_calls.append(a) or ""), \
             mock.patch.object(agent.backend, "prompt", return_value=ai) as prompt, \
             mock.patch.object(pr.describe, "_apply_body",
                               side_effect=lambda r, n, b: self.bodies.append(b) or True), \
             mock.patch.object(pr.describe, "_apply_title",
                               side_effect=lambda r, n, t: self.titles.append(t) or True):
            self.rc = pr.describe.run_describe(_ctx(worktree, base), opts)
        self.prompt = prompt


# ── U1: the body is revised; the title only moves with --title ─────────────


def test_u1_the_ai_revises_the_body_and_leaves_the_title(worktree):
    run = Run(worktree)
    assert run.rc == 0
    assert run.bodies == ["NEW BODY"]
    assert run.titles == []
    assert "revising the description" in run.prompt.call_args[0][0]


def test_u1_title_override_sets_the_title(worktree):
    run = Run(worktree, DescribeOptions(title="feat: new"))
    assert run.titles == ["feat: new"]


# ── U2: nothing reaches GitHub without --post ───────────────────────────────


def test_u2_a_title_is_drafted_with_the_gate_closed(capsys):
    with mock.patch.object(gh.client, "run") as run:
        assert pr.describe._apply_title("owner/repo", 7, "feat: new") is False
    run.assert_not_called()
    assert "DRAFT" in capsys.readouterr().err


def test_u2_a_title_is_edited_with_the_gate_open(publishing_on):
    with mock.patch.object(gh.client, "run",
                           return_value=mock.MagicMock(ok=True)) as run:
        assert pr.describe._apply_title("owner/repo", 7, "feat: new") is True
    assert run.call_args[0] == ("pr", "edit", "7", "--repo", "owner/repo",
                                "--title", "feat: new")


def test_u2_a_draft_run_with_closes_is_not_a_failure(worktree, monkeypatch):
    _provider(monkeypatch, IssueProvider.GITHUB)
    with mock.patch.object(pr.describe, "_fetch_pr_body", return_value=("t", "b")), \
         mock.patch.object(pr.describe, "_git", return_value=""), \
         mock.patch.object(agent.backend, "prompt",
                           return_value=(pr.describe._NO_CHANGE, 0)), \
         mock.patch.object(gh.client, "run") as run:
        rc = pr.describe.run_describe(_ctx(worktree), DescribeOptions(closes=("941",)))
    assert rc == 0
    run.assert_not_called()


# ── U3: the PR's own base measures the branch ───────────────────────────────


def test_u3_a_pr_targeting_release_measures_against_origin_release(worktree):
    run = Run(worktree, base="release")
    assert ("log", "--oneline", "origin/release..HEAD") in run.git_calls
    assert ("diff", "--name-only", "origin/release...HEAD") in run.git_calls


def test_u3_no_known_base_falls_back_to_the_default_branch(worktree):
    with mock.patch.object(git.topology, "default_branch", return_value="trunk"):
        run = Run(worktree)
    assert ("log", "--oneline", "origin/trunk..HEAD") in run.git_calls


def test_the_base_is_fetched_before_the_range_is_measured(worktree, monkeypatch, capsys):
    monkeypatch.setattr(pr.describe, "_fetch_refusal", _REAL_FETCH)
    failed = mock.MagicMock(ok=False, detail="couldn't find remote ref release",
                            returncode=128)
    with mock.patch.object(git.client, "run", return_value=failed) as run:
        r = Run(worktree, base="release")
    assert r.rc == 1
    assert not r.prompt.called
    assert r.git_calls == []
    assert run.call_args[0] == ("fetch", "origin", "release", "--quiet")
    assert ("✗ Could not fetch origin/release: couldn't find remote ref release"
            in capsys.readouterr().err)


def test_fetch_succeeds_but_the_base_does_not_resolve(worktree, monkeypatch, capsys):
    """A remote with no fetch refspec fetches into FETCH_HEAD only, so the
    fetch exits zero and `origin/<base>` still does not resolve — the worktree
    has no origin remote at all, so this is exactly that case."""
    monkeypatch.setattr(pr.describe, "_fetch_refusal", _REAL_FETCH)
    ok = mock.MagicMock(ok=True)
    with mock.patch.object(git.client, "run", return_value=ok):
        r = Run(worktree, base="release")
    assert r.rc == 1
    assert not r.prompt.called
    assert r.git_calls == []
    assert ("✗ origin/release does not resolve — cannot measure a description "
            "against a base that doesn't exist") in capsys.readouterr().err


def test_a_hand_body_does_not_fetch(worktree, monkeypatch):
    calls = []
    monkeypatch.setattr(pr.describe, "_fetch_refusal",
                        lambda wt, base: calls.append(base) or "✗ no")
    run = Run(worktree, DescribeOptions(body="hand body"))
    assert run.rc == 0
    assert calls == []


# ── U4: --issue is gone ─────────────────────────────────────────────────────


def test_u4_issue_is_not_a_flag():
    with pytest.raises(SystemExit) as exc:
        cli.pr_describe.build_parser().parse_args(["--issue", "ENG-1"])
    assert exc.value.code == 2


# ── U5: explicit intent bypasses the HEAD gate ──────────────────────────────


def test_u5_closes_bypasses_the_head_gate(worktree, monkeypatch):
    _provider(monkeypatch, IssueProvider.GITHUB)
    _described_at_head(worktree)
    run = Run(worktree, DescribeOptions(closes=("941",)), body="## What\n")
    assert run.rc == 0
    assert run.bodies and run.bodies[0].endswith("Closes #941")


def test_u5_title_bypasses_the_head_gate(worktree):
    _described_at_head(worktree)
    run = Run(worktree, DescribeOptions(title="feat: t"))
    assert run.rc == 0
    assert run.titles == ["feat: t"]


def test_u5_body_bypasses_the_head_gate(worktree):
    _described_at_head(worktree)
    run = Run(worktree, DescribeOptions(body="hand body"))
    assert run.rc == 0
    assert run.bodies == ["hand body"]


def test_without_overrides_the_head_gate_still_skips(worktree):
    _described_at_head(worktree)
    run = Run(worktree)
    assert run.rc == 0
    assert not run.prompt.called
    assert run.bodies == []


# ── a draft does not stand in for a published run ──────────────────────────


def test_a_draft_then_post_at_the_same_head_posts_without_force(worktree, monkeypatch):
    first = Run(worktree)
    assert first.prompt.called
    assert pr.state.load_state(worktree / "target").describe.published is False
    import core.publishing
    monkeypatch.setattr(core.publishing, "_enabled", True)
    second = Run(worktree)
    assert second.prompt.called
    assert second.bodies == ["NEW BODY"]
    assert pr.state.load_state(worktree / "target").describe.published is True


def test_post_then_post_at_the_same_head_skips(worktree, publishing_on):
    first = Run(worktree)
    assert first.bodies == ["NEW BODY"]
    second = Run(worktree)
    assert not second.prompt.called
    assert second.bodies == []


def test_a_no_change_run_then_post_skips(worktree, publishing_on):
    Run(worktree, ai=(pr.describe._NO_CHANGE, 0))
    assert pr.state.load_state(worktree / "target").describe.published is True
    second = Run(worktree)
    assert not second.prompt.called


def test_state_without_the_published_field_reads_as_unpublished(worktree):
    state = pr.state.new_state("owner/repo", "b", pr_number=7, head_sha="aaaa111",
                               worktree_root=str(worktree))
    pr.state.apply(state, pr.domains.DescribeSummary(head_sha="aaaa111"))
    pr.state.save_state(worktree / "target", state)
    run = Run(worktree)
    assert run.prompt.called


# ── no PR ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("opts", [DescribeOptions(title="feat: t"),
                                  DescribeOptions(body="b"),
                                  DescribeOptions(closes=("941",))])
def test_overrides_without_a_pr_are_refused(worktree, capsys, opts):
    ctx = make_ctx(branch="isaac/feat/x", pr_number=None, worktree_root=worktree,
                   head_sha="aaaa111", target_dir=worktree / "target")
    assert pr.describe.run_describe(ctx, opts) == 1
    assert ("✗ No PR found for isaac/feat/x — --title/--body/--closes need one; "
            "use pr create") in capsys.readouterr().err


def test_no_pr_and_no_overrides_is_still_a_no_op(worktree, capsys):
    ctx = make_ctx(branch="isaac/feat/x", pr_number=None, worktree_root=worktree,
                   head_sha="aaaa111", target_dir=worktree / "target")
    assert pr.describe.run_describe(ctx) == 0
    assert "nothing to describe" in capsys.readouterr().err


def test_closes_at_a_published_head_skips_the_ai_revision(worktree, monkeypatch):
    _provider(monkeypatch, IssueProvider.GITHUB)
    _described_at_head(worktree)
    run = Run(worktree, DescribeOptions(closes=("941",)))
    assert run.rc == 0
    assert not run.prompt.called
    assert run.bodies == ["old body\n\nCloses #941"]


def test_title_at_a_published_head_skips_the_ai_revision(worktree):
    _described_at_head(worktree)
    run = Run(worktree, DescribeOptions(title="feat: t"))
    assert not run.prompt.called
    assert run.titles == ["feat: t"]
    assert run.bodies == []


def test_title_only_at_a_published_head_records_no_body_change(worktree):
    """The body never moved, so the persisted summary must not claim it did."""
    _described_at_head(worktree)
    Run(worktree, DescribeOptions(title="feat: t"), body="old body")
    assert pr.state.load_state(worktree / "target").describe.changed is False


def test_force_with_closes_still_revises(worktree, monkeypatch):
    _provider(monkeypatch, IssueProvider.GITHUB)
    _described_at_head(worktree)
    run = Run(worktree, DescribeOptions(closes=("941",), force=True))
    assert run.prompt.called
    assert run.bodies == ["NEW BODY\n\nCloses #941"]


# ── --closes ────────────────────────────────────────────────────────────────


def test_closes_941_appends_a_closing_line(worktree, monkeypatch, capsys):
    _provider(monkeypatch, IssueProvider.GITHUB)
    run = Run(worktree, DescribeOptions(closes=("941",)))
    assert run.bodies == ["NEW BODY\n\nCloses #941"]
    assert "Linked for auto-close on merge: Closes #941" in capsys.readouterr().err


def test_a_tracker_key_is_refused_unless_linear(worktree, monkeypatch, capsys):
    _provider(monkeypatch, IssueProvider.GITHUB)
    run = Run(worktree, DescribeOptions(closes=("ENG-12",)))
    assert run.rc == 1
    assert not run.prompt.called
    assert run.bodies == []
    assert "✗ --closes ENG-12" in capsys.readouterr().err


def test_a_tracker_key_closes_on_linear(worktree, monkeypatch):
    _provider(monkeypatch, IssueProvider.LINEAR)
    run = Run(worktree, DescribeOptions(closes=("ENG-12",)))
    assert run.bodies == ["NEW BODY\n\nCloses ENG-12"]


def test_an_unreadable_config_refuses_closes(worktree, monkeypatch, capsys):
    def broken(wt):
        raise config.workbench_config.ConfigError("bad config.yml")
    monkeypatch.setattr(config.workbench_config, "load_config", broken)
    run = Run(worktree, DescribeOptions(closes=("941",)))
    assert run.rc == 1
    assert "✗ bad config.yml" in capsys.readouterr().err


def test_no_change_plus_closes_still_writes(worktree, monkeypatch):
    _provider(monkeypatch, IssueProvider.GITHUB)
    run = Run(worktree, DescribeOptions(closes=("941",)), body="kept body",
              ai=(pr.describe._NO_CHANGE, 0))
    assert run.bodies == ["kept body\n\nCloses #941"]


def test_closes_already_in_the_body_writes_nothing(worktree, monkeypatch):
    _provider(monkeypatch, IssueProvider.GITHUB)
    run = Run(worktree, DescribeOptions(closes=("941",)), body="b\n\nCloses #941",
              ai=(pr.describe._NO_CHANGE, 0))
    assert run.rc == 0
    assert run.bodies == []


# ── preservation ────────────────────────────────────────────────────────────


def test_preserve_restores_a_ref_the_ai_dropped(worktree, capsys):
    run = Run(worktree, body="old\n\nFixes #12", ai=(_wrapped("rewritten"), 0))
    assert run.bodies == ["rewritten\n\nCloses #12"]
    assert "Preserved existing issue link(s): Closes #12" in capsys.readouterr().err


def test_preserve_restores_a_ref_a_hand_body_dropped(worktree):
    run = Run(worktree, DescribeOptions(body="hand"), body="old\n\nCloses #12")
    assert run.bodies == ["hand\n\nCloses #12"]


# ── --body ──────────────────────────────────────────────────────────────────


def test_an_off_template_body_is_refused_without_an_ai_call(worktree, capsys):
    (worktree / ".github").mkdir()
    (worktree / ".github" / "pull_request_template.md").write_text(TEMPLATE)
    run = Run(worktree, DescribeOptions(body="## What\nonly half"))
    assert run.rc == 1
    assert not run.prompt.called
    assert run.bodies == []
    err = capsys.readouterr().err
    assert "does not use this repo's template" in err
    assert "## Why" in err


def test_an_on_template_body_replaces_wholesale(worktree):
    (worktree / ".github").mkdir()
    (worktree / ".github" / "pull_request_template.md").write_text(TEMPLATE)
    body = "## What\n\nx\n\n## Why\n\ny"
    run = Run(worktree, DescribeOptions(body=body), body="something else")
    assert run.rc == 0
    assert not run.prompt.called
    assert run.bodies == [body]


# ── dry run ─────────────────────────────────────────────────────────────────


def test_dry_run_prints_title_and_body_and_writes_nothing(worktree, monkeypatch, capsys):
    _provider(monkeypatch, IssueProvider.GITHUB)
    run = Run(worktree, DescribeOptions(dry_run=True, title="feat: t", closes=("941",)))
    assert run.rc == 0
    assert run.bodies == [] and run.titles == []
    out = capsys.readouterr().out
    assert "feat: t" in out
    assert "NEW BODY\n\nCloses #941" in out
    assert pr.state.load_state(worktree / "target") is None


# ── the command line ────────────────────────────────────────────────────────


class Main:
    """`cli.pr_describe.main` with resolution, the lock and the trail stubbed."""

    def __init__(self, worktree, *argv, token=None):
        self.opts = None
        self.token_calls = []

        def describe(ctx, opts, *, trail=None):
            self.opts = opts
            return 0

        def use_for_publishing(wt):
            self.token_calls.append(wt)
            if token is not None:
                raise token

        with mock.patch.object(pr.context, "resolve", return_value=_ctx(worktree)), \
             mock.patch.object(core.run_lock, "claim_for_process"), \
             mock.patch.object(cli.pr_describe, "Trail"), \
             mock.patch.object(pr.gh_token, "use_for_publishing",
                               side_effect=use_for_publishing), \
             mock.patch.object(pr.describe, "run_describe", side_effect=describe):
            self.rc = cli.pr_describe.main(list(argv))


def test_cli_passes_the_overrides_through(worktree):
    main = Main(worktree, "--title", "feat: t", "--body", "b",
                "--closes", "941", "--closes", "#942", "--force")
    assert main.rc == 0
    assert main.opts == DescribeOptions(force=True, title="feat: t", body="b",
                                        closes=("941", "#942"))


def test_cli_reads_the_body_file(worktree, tmp_path):
    path = tmp_path / "body.md"
    path.write_text("from a file\n")
    main = Main(worktree, "--body-file", str(path))
    assert main.opts.body == "from a file\n"


def test_cli_an_unreadable_body_file_exits_2(worktree, tmp_path, capsys):
    missing = tmp_path / "nope.md"
    main = Main(worktree, "--body-file", str(missing))
    assert main.rc == 2
    assert main.opts is None
    assert f"✗ --body-file {missing}: No such file or directory" in capsys.readouterr().err


def test_cli_body_and_body_file_are_exclusive(worktree, tmp_path):
    with pytest.raises(SystemExit) as exc:
        cli.pr_describe.build_parser().parse_args(
            ["--body", "b", "--body-file", str(tmp_path / "x")])
    assert exc.value.code == 2


def test_cli_no_issue_is_accepted_and_inert(worktree):
    main = Main(worktree, "--no-issue")
    assert main.rc == 0
    assert main.opts == DescribeOptions()


# ── U6: the publishing token, only under --post ─────────────────────────────


def test_u6_post_claims_the_publishing_token(worktree):
    main = Main(worktree, "--post")
    assert main.token_calls == [worktree]
    assert main.opts is not None


def test_u6_a_draft_run_needs_no_token(worktree):
    main = Main(worktree)
    assert main.token_calls == []


def test_u6_a_dry_run_claims_no_token_even_with_post(worktree):
    main = Main(worktree, "--post", "--dry-run")
    assert main.token_calls == []
    assert main.opts is not None and main.opts.dry_run


def test_u6_no_token_refuses_before_describing(worktree, capsys):
    main = Main(worktree, "--post",
                token=pr.gh_token.TokenNotConfigured("set GH_TOKEN"))
    assert main.rc == 1
    assert main.opts is None
    assert "set GH_TOKEN" in capsys.readouterr().err


def test_u6_an_unreadable_token_file_refuses(worktree, capsys):
    main = Main(worktree, "--post",
                token=PermissionError(13, "Permission denied", "/cfg/taskfile.env"))
    assert main.rc == 1
    assert main.opts is None
    assert "✗ Could not read /cfg/taskfile.env: Permission denied" in capsys.readouterr().err


def test_u6_a_bad_closes_is_refused_before_the_token_is_claimed(worktree, monkeypatch, capsys):
    """A `--closes` nothing can close costs no token lookup, as in `pr create`.

    The token claim spends gh calls and, when it fails, reports a token
    problem — the wrong error for a run whose real fault is its own flag.
    """
    _provider(monkeypatch, IssueProvider.GITHUB)
    main = Main(worktree, "--post", "--closes", "ENG-12",
                token=pr.gh_token.TokenNotConfigured("set GH_TOKEN"))
    err = capsys.readouterr().err
    assert main.rc == 1
    assert main.token_calls == []
    assert main.opts is None
    assert "✗ --closes ENG-12" in err
    assert "set GH_TOKEN" not in err
