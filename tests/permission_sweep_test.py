"""Tests for lib/permission_sweep.py — the machine-wide grant-drift report.

The matcher and the coverage test are `bin/local/validate-permissions`' and are
covered by tests/validate_permissions_test.py. What is new here is the sweep's
own three: coverage measured against the machine-wide file as well as the repo's
tracked one, staleness, and walking the project registry without visiting one
container twice.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

import permission_sweep as ps  # noqa: E402
import permissions as perms  # noqa: E402

# The two rule sources a grant can be covered by. `bin/*` stands in for a repo's
# tracked .claude/settings.json and `gh pr:*` for ~/.claude/settings.json, which
# is the only one most registered repos have.
TRACKED = perms.TrackedRules(allow=["bin/*"], ask=["bin/get-secret:*"])
MACHINE = perms.TrackedRules(allow=["gh pr:*"], ask=["ai/claude/bin/pr:*"])


def _local(directory, *rules, bucket="allow"):
    """An untracked settings file holding rules, the way Claude Code writes one."""
    path = Path(directory) / perms.LOCAL_SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"permissions": {bucket: list(rules)}}, indent=2))
    return str(path)


def _scan(directory, *rules, prune=False):
    return ps.scan_file(_local(directory, *rules), TRACKED, MACHINE, prune)


# ── staleness ────────────────────────────────────────────────────────────


def test_a_rule_naming_a_live_directory_is_not_stale(tmp_path):
    assert ps._named_directory(f"{tmp_path}/thing:*") is None


def test_a_missing_file_in_a_live_directory_is_not_stale(tmp_path):
    """A prefix rule names a string, not a file — `bin/run:*` is live either way."""
    assert ps._named_directory(f"{tmp_path}/never-existed") is None


def test_a_path_whose_directory_is_gone_is_stale(tmp_path):
    gone = tmp_path / "worktree"
    assert ps._named_directory(f"{gone}/bin/script") == str(gone)


def test_the_report_names_the_top_of_the_gone_subtree(tmp_path):
    """What went away is a worktree, not the four levels of path below it."""
    gone = tmp_path / "worktree"
    assert ps._named_directory(f"{gone}/a/b/c/d") == str(gone)


def test_a_glob_is_cut_before_the_directory_test(tmp_path):
    gone = tmp_path / "venv"
    assert ps._named_directory(f"{gone}/bin/pip install *") == str(gone)


def test_a_path_behind_an_env_var_prefix_is_read(tmp_path):
    """`VAR=/path cmd` is how half of these grants are written."""
    gone = tmp_path / "checkout"
    assert ps._named_directory(f"PYTHONPATH={gone}/lib python3 -m pytest") == str(gone)


def test_a_relative_path_is_never_judged():
    """`bin/local/validate-all` names no directory this could test."""
    assert ps._named_directory("bin/local/validate-all") is None


def test_a_root_level_token_is_left_alone():
    assert ps._named_directory("/nonexistent-at-root") is None


def test_stale_grants_carry_the_line_they_are_written_on(tmp_path):
    gone = tmp_path / "gone"
    path = _local(tmp_path, "Bash(printenv)", f"Bash({gone}/bin/x)")
    assert [(s.missing, s.line) for s in ps.stale_in(path, set())] == [(str(gone), 5)]


def test_a_classified_grant_is_not_also_reported_as_stale(tmp_path):
    """It already has a named home and a named fix; two entries only cost the reader."""
    gone = tmp_path / "gone"
    rule = f"Bash({gone}/bin/x)"
    assert ps.stale_in(_local(tmp_path, rule), {rule}) == []


def test_an_unreadable_settings_file_yields_no_stale_grants(tmp_path):
    path = tmp_path / perms.LOCAL_SETTINGS
    path.write_text("{ not json")
    assert ps.stale_in(str(path), set()) == []


# ── coverage from two rule sources ───────────────────────────────────────


def test_a_grant_the_repos_tracked_file_makes_is_covered(tmp_path):
    assert [(g.rule, g.tracked) for g in _scan(tmp_path, "Bash(bin/local/validate-all)").covered] \
        == [("Bash(bin/local/validate-all)", "Bash(bin/*)")]


def test_a_grant_the_machine_file_makes_is_covered(tmp_path):
    """The signal that works in a repo `ai init` left with no tracked settings."""
    assert [(g.rule, g.tracked) for g in _scan(tmp_path, "Bash(gh pr view 12)").covered] == [
        ("Bash(gh pr view 12)", "Bash(gh pr:*)")
    ]


def test_a_repo_with_no_tracked_file_still_gets_machine_coverage(tmp_path):
    path = _local(tmp_path, "Bash(gh pr view 12)")
    report = ps.scan_file(path, perms.TrackedRules(), MACHINE, False)
    assert [g.rule for g in report.covered] == ["Bash(gh pr view 12)"]


def test_a_grant_covered_by_both_files_is_reported_once(tmp_path):
    both = perms.TrackedRules(allow=["gh pr:*"])
    report = ps.scan_file(_local(tmp_path, "Bash(gh pr view 12)"), both, MACHINE, False)
    assert len(report.covered) == 1


def test_the_repos_own_file_wins_the_attribution(tmp_path):
    """Of two true answers, the one naming a file in this repo is the useful one."""
    tracked = perms.TrackedRules(allow=["gh:*"])
    report = ps.scan_file(_local(tmp_path, "Bash(gh pr view 12)"), tracked, MACHINE, False)
    assert [g.tracked for g in report.covered] == ["Bash(gh:*)"]


def test_a_machine_ask_rule_makes_a_local_allow_an_override(tmp_path):
    report = _scan(tmp_path, "Bash(ai/claude/bin/pr status)")
    assert [(g.rule, g.tracked) for g in report.overrides] == [
        ("Bash(ai/claude/bin/pr status)", "Bash(ai/claude/bin/pr:*)")
    ]
    assert report.covered == []


def test_an_override_outranks_a_covered_reading_of_the_same_rule(tmp_path):
    """An ask anywhere means a person is meant to see the call; an allow
    elsewhere does not cancel that."""
    tracked = perms.TrackedRules(allow=["ai/claude/bin/*"])
    report = ps.scan_file(_local(tmp_path, "Bash(ai/claude/bin/pr status)"), tracked,
                          MACHINE, False)
    assert [g.rule for g in report.overrides] == ["Bash(ai/claude/bin/pr status)"]
    assert report.covered == []


def test_a_one_off_grant_is_neither_covered_nor_stale(tmp_path):
    assert _scan(tmp_path, "Bash(printenv)", "Bash(uv run *)").empty


def test_the_grant_total_counts_every_bash_allow_rule(tmp_path):
    report = _scan(tmp_path, "Bash(printenv)", "Bash(bin/x)", "WebFetch(domain:example.com)")
    assert report.grants == 2


def test_an_unreadable_settings_file_costs_no_other_repo(tmp_path):
    """The sweep visits repos nobody asked it to touch; one bad file is not a stop."""
    path = tmp_path / perms.LOCAL_SETTINGS
    path.write_text("{ not json")
    assert ps.scan_file(str(path), TRACKED, MACHINE, False).empty


# ── pruning ──────────────────────────────────────────────────────────────


def _allow_after(path):
    return json.loads(Path(path).read_text())["permissions"]["allow"]


def test_prune_deletes_only_the_covered_class(tmp_path):
    gone = tmp_path / "gone"
    kept = ["Bash(printenv)", "Bash(ai/claude/bin/pr status)", f"Bash({gone}/bin/x)"]
    path = _local(tmp_path, "Bash(bin/local/validate-all)", *kept)

    ps.scan_file(path, TRACKED, MACHINE, True)

    assert _allow_after(path) == kept


def test_prune_reports_what_it_deleted(tmp_path):
    report = _scan(tmp_path, "Bash(bin/local/validate-all)", prune=True)
    assert [g.rule for g in report.pruned] == ["Bash(bin/local/validate-all)"]
    assert report.covered == []


def test_the_report_counts_grants_as_they_were_before_the_prune(tmp_path):
    """Otherwise the line reads `1 grant(s)` beside `pruned 1`, which reads as zero."""
    assert _scan(tmp_path, "Bash(bin/local/validate-all)", "Bash(printenv)",
                 prune=True).grants == 2


def test_without_prune_the_file_is_untouched(tmp_path):
    path = _local(tmp_path, "Bash(bin/local/validate-all)")
    before = Path(path).read_text()
    ps.scan_file(path, TRACKED, MACHINE, False)
    assert Path(path).read_text() == before


def test_prune_is_idempotent(tmp_path):
    path = _local(tmp_path, "Bash(bin/local/validate-all)", "Bash(printenv)")
    ps.scan_file(path, TRACKED, MACHINE, True)
    once = Path(path).read_text()
    ps.scan_file(path, TRACKED, MACHINE, True)
    assert Path(path).read_text() == once


# ── the machine-wide rule source ─────────────────────────────────────────


def test_machine_rules_read_the_home_settings_file(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash(gh:*)"],
                                                    "ask": ["Bash(bin/get-secret:*)"]}}))
    rules = perms.machine_rules(str(tmp_path))
    assert rules.allow == ["gh:*"]
    assert rules.ask == ["bin/get-secret:*"]


def test_a_home_with_no_settings_file_grants_nothing(tmp_path):
    assert perms.machine_rules(str(tmp_path)) == perms.TrackedRules()


# ── walking the registry ─────────────────────────────────────────────────

_GIT_TIMEOUT = 10  # seconds; a hang here should fail the test, not stall the suite


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   timeout=_GIT_TIMEOUT)


@pytest.fixture
def container(tmp_path):
    """The bare-repo worktree layout: two worktrees as peers of a bare `.git`."""
    seed = tmp_path / "seed"
    seed.mkdir()
    subprocess.run(["git", "init", "-b", "main", "-q", str(seed)], check=True,
                   capture_output=True, timeout=_GIT_TIMEOUT)
    _git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
         "--no-verify", "-m", "init")
    root = tmp_path / "container"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(root / ".git")], check=True,
                   capture_output=True, timeout=_GIT_TIMEOUT)
    _git(root / ".git", "worktree", "add", "-q", str(root / "main"), "main")
    _git(root / ".git", "worktree", "add", "-q", str(root / "feature"), "-b", "feature")
    return root


def test_the_container_above_a_worktree_is_swept(container):
    _local(container / ".claude", "Bash(gh pr view 12)")
    files = ps.local_settings_files(str(container / "main"))
    assert [Path(f).parent.parent.name for f in files] == ["container"]


def test_a_worktrees_tracked_file_is_not_swept(container):
    tracked = container / "main" / ".claude" / "settings.json"
    tracked.parent.mkdir(parents=True)
    tracked.write_text(json.dumps({"permissions": {"allow": ["Bash(bin/*)"]}}))
    assert ps.local_settings_files(str(container / "main")) == []


def test_a_container_shared_by_sibling_worktrees_is_scanned_once(container):
    """Both worktrees reach the same file; reporting it twice would double the counts."""
    _local(container / ".claude", "Bash(gh pr view 12)")
    roots = [str(container / "main"), str(container / "feature")]
    reports = ps.sweep(roots, MACHINE)
    assert [r.root for r in reports] == [str(container / "main")]
    assert sum(len(r.files) for r in reports) == 1


def test_a_repo_with_no_untracked_settings_is_left_out_of_the_report(container):
    assert ps.sweep([str(container / "main")], MACHINE) == []


def test_the_sweep_classifies_against_the_repo_it_found_the_file_under(container):
    (container / "main" / ".claude").mkdir(parents=True)
    (container / "main" / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(bin/*)"]}})
    )
    _local(container / ".claude", "Bash(bin/local/validate-all)")

    reports = ps.sweep([str(container / "main")], perms.TrackedRules())

    assert [g.tracked for g in reports[0].files[0].covered] == ["Bash(bin/*)"]


# ── CLI ──────────────────────────────────────────────────────────────────


def test_main_exits_0_when_it_finds_drift(container, monkeypatch, capsys):
    """It reports; `otto-workbench maintenance` must not start failing over drift."""
    _local(container / ".claude", "Bash(gh pr view 12)")
    monkeypatch.setattr(ps.workbench_projects, "registered",
                        lambda: [Path(container / "main")])
    monkeypatch.setattr(ps, "machine_rules", lambda home: MACHINE)

    assert ps.main([]) == 0
    assert "1 registered repo(s), 1 with drift" in capsys.readouterr().out


def test_verbose_lists_every_grant(container, monkeypatch, capsys):
    _local(container / ".claude", "Bash(gh pr view 12)")
    monkeypatch.setattr(ps.workbench_projects, "registered",
                        lambda: [Path(container / "main")])
    monkeypatch.setattr(ps, "machine_rules", lambda home: MACHINE)

    ps.main(["--verbose"])

    assert "Bash(gh pr view 12)" in capsys.readouterr().out


def test_prune_from_the_cli_deletes_the_covered_class(container, monkeypatch, capsys):
    path = _local(container / ".claude", "Bash(gh pr view 12)", "Bash(printenv)")
    monkeypatch.setattr(ps.workbench_projects, "registered",
                        lambda: [Path(container / "main")])
    monkeypatch.setattr(ps, "machine_rules", lambda home: MACHINE)

    ps.main(["--prune"])

    assert _allow_after(path) == ["Bash(printenv)"]


def test_an_empty_registry_says_so(monkeypatch, capsys):
    monkeypatch.setattr(ps.workbench_projects, "registered", lambda: [])
    assert ps.main([]) == 0
    assert "No repos registered yet" in capsys.readouterr().out
