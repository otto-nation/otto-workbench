"""Tests for bin/local/validate-permissions."""

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-permissions"

_loader = importlib.machinery.SourceFileLoader("validate_permissions", str(SCRIPT))
_spec = importlib.util.spec_from_loader("validate_permissions", _loader)
vp = importlib.util.module_from_spec(_spec)
sys.modules["validate_permissions"] = vp
_spec.loader.exec_module(vp)


def _dead(*rules):
    return [(v.rule, v.fix) for v in vp.check_rules(list(rules))]


# ── live rules ───────────────────────────────────────────────────────────


def test_a_plain_prefix_rule_is_live():
    assert vp.check_rules(["Bash(git:*)", "Bash(gh pr:*)", "Bash(npm config set:*)"]) == []


def test_a_wildcard_rule_is_live():
    assert vp.check_rules(["Bash(bin/local/*)", "Bash(* --help)", "Bash(claude *)"]) == []


def test_an_exact_rule_is_live():
    assert vp.check_rules(["Bash(env)"]) == []


def test_a_punctuation_prefix_is_live():
    """Bash(\\[:*) covers the test builtin — the bracket is not a glob."""
    assert vp.check_rules(["Bash([:*)", "Bash([[:*)"]) == []


def test_non_bash_rules_are_left_alone():
    """Path rules do expand ~ and do glob, so their syntax is not ours to judge."""
    assert vp.check_rules(["Read(~/.claude/**)", "Edit(//tmp/**)", "WebSearch"]) == []


# ── dead rules ───────────────────────────────────────────────────────────


def test_a_star_inside_a_prefix_is_dead():
    assert _dead("Bash(bin/local/*:*)") == [("Bash(bin/local/*:*)", "Bash(bin/local/*)")]


def test_the_reason_names_prefix_matching():
    assert "prefix matching" in vp.check_rules(["Bash(bin/local/*:*)"])[0].reason


def test_a_leading_tilde_in_a_prefix_rule_is_dead():
    """Both faults at once: the tilde stays literal and the ** never globs."""
    assert _dead("Bash(~/.claude/skills/**:*)") == [
        ("Bash(~/.claude/skills/**:*)", "Bash(/*/.claude/skills/*)")
    ]


def test_a_leading_tilde_in_a_wildcard_rule_is_dead():
    assert _dead("Bash(~/.local/bin/*)") == [("Bash(~/.local/bin/*)", "Bash(/*/.local/bin/*)")]


def test_the_suggested_fix_stays_anchored_at_the_root():
    """A bare leading * would also match the path after a pipe into a shell."""
    assert _dead("Bash(~/bin/x:*)")[0][1].startswith("Bash(/*/")


def test_an_empty_prefix_is_dead():
    rule = "Bash(:*)"
    assert _dead(rule) == [(rule, vp.NAME_THE_COMMAND)]


def test_a_suggested_fix_gains_a_trailing_star():
    """Wildcard rules are anchored, so the bare form would reject any argument."""
    assert _dead("Bash(~/bin/deploy:*)") == [("Bash(~/bin/deploy:*)", "Bash(/*/bin/deploy*)")]


def test_every_dead_rule_in_a_list_is_reported():
    dead = _dead("Bash(git:*)", "Bash(bin/local/*:*)", "Bash(~/.claude/skills/**:*)")
    assert [rule for rule, _ in dead] == ["Bash(bin/local/*:*)", "Bash(~/.claude/skills/**:*)"]


# ── settings files ───────────────────────────────────────────────────────


def _settings(tmp_path, body):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(body, indent=2))
    return path


def test_every_bucket_is_scanned(tmp_path):
    path = _settings(tmp_path, {
        "permissions": {
            "allow": ["Bash(a/*:*)"],
            "deny": ["Bash(b/*:*)"],
            "ask": ["Bash(c/*:*)"],
        },
    })
    assert [v.rule for v in vp.check_file(str(path))] == [
        "Bash(a/*:*)", "Bash(b/*:*)", "Bash(c/*:*)"
    ]


def test_a_rule_repeated_across_buckets_is_reported_once(tmp_path):
    path = _settings(tmp_path, {
        "permissions": {"allow": ["Bash(a/*:*)"], "deny": ["Bash(a/*:*)"]},
    })
    assert len(vp.check_file(str(path))) == 1


def test_violations_carry_the_line_they_are_written_on(tmp_path):
    path = _settings(tmp_path, {"permissions": {"allow": ["Bash(git:*)", "Bash(a/*:*)"]}})
    assert [v.line for v in vp.check_file(str(path))] == [5]


def test_a_settings_file_with_no_permissions_block_is_clean(tmp_path):
    path = _settings(tmp_path, {"statusLine": {"command": "x"}})
    assert vp.check_file(str(path)) == []


# ── local grant drift ────────────────────────────────────────────────────
# The tracked project file grants three bin directories and gates two scripts
# that reach credentials; these stand in for it so the tests do not move when
# the real file gains a directory.

TRACKED = vp.TrackedRules(allow=["bin/*", "ai/claude/bin/*"], ask=["bin/get-secret:*"])


def _local(tmp_path, *allow, bucket="allow"):
    path = tmp_path / vp.LOCAL_SETTINGS
    path.write_text(json.dumps({"permissions": {bucket: list(allow)}}, indent=2))
    return str(path)


def _covered(tmp_path, *allow):
    return [(g.rule, g.tracked) for g in vp.drift_in(_local(tmp_path, *allow), TRACKED).covered]


def _overrides(tmp_path, *allow):
    return [(g.rule, g.tracked) for g in vp.drift_in(_local(tmp_path, *allow), TRACKED).overrides]


def test_a_grant_under_a_granted_directory_is_covered(tmp_path):
    assert _covered(tmp_path, "Bash(bin/local/validate-all)") == [
        ("Bash(bin/local/validate-all)", "Bash(bin/*)")
    ]


def test_a_grant_carrying_arguments_is_covered(tmp_path):
    """The tracked wildcard ends in `*`, so the whole invocation is inside it."""
    assert _covered(tmp_path, "Bash(ai/claude/bin/pr --tool-schema)") == [
        ("Bash(ai/claude/bin/pr --tool-schema)", "Bash(ai/claude/bin/*)")
    ]


def test_a_local_wildcard_under_a_granted_directory_is_covered(tmp_path):
    """Every command it can match starts with `bin/`, so the tracked rule has it."""
    assert _covered(tmp_path, "Bash(bin/local/validate-ceiling *)") == [
        ("Bash(bin/local/validate-ceiling *)", "Bash(bin/*)")
    ]


def test_a_one_off_grant_has_no_tracked_home(tmp_path):
    """Nothing tracked speaks for these, and nagging trains the warning away."""
    assert _covered(tmp_path, "Bash(sh /tmp/probe.sh)", "Bash(uv run *)",
                    "Bash(ps ax *)", "Bash(printenv)") == []


def test_a_non_bash_grant_is_left_alone(tmp_path):
    """The tracked file grants Bash rules only, so a domain has nowhere to go."""
    assert _covered(tmp_path, "WebFetch(domain:example.com)", "Read(//Users/x/.ssh/**)") == []


def test_a_local_rule_broader_than_the_tracked_one_is_not_flagged(tmp_path):
    """It grants commands the tracked rule does not, so it is not a duplicate."""
    assert _covered(tmp_path, "Bash(bin*)", "Bash(*)", "Bash(b:*)") == []


def test_a_local_prefix_rule_is_not_judged_against_an_exact_tracked_rule(tmp_path):
    """`Bash(env:*)` reaches `env -i sh`, which `Bash(env)` never grants."""
    tracked = vp.TrackedRules(allow=["env"])
    assert vp.drift_in(_local(tmp_path, "Bash(env:*)"), tracked).covered == []


def test_an_exactly_repeated_tracked_rule_is_covered(tmp_path):
    tracked = vp.TrackedRules(allow=["env"])
    assert [g.rule for g in vp.drift_in(_local(tmp_path, "Bash(env)"), tracked).covered] == [
        "Bash(env)"
    ]


def test_a_local_allow_over_a_gated_script_is_an_override(tmp_path):
    assert _overrides(tmp_path, "Bash(bin/get-secret --name prod/db)") == [
        ("Bash(bin/get-secret --name prod/db)", "Bash(bin/get-secret:*)")
    ]


def test_an_override_outranks_the_covered_warning(tmp_path):
    """`Bash(bin/*)` covers it too — the credential gate is the finding that counts."""
    drift = vp.drift_in(_local(tmp_path, "Bash(bin/get-secret:*)"), TRACKED)
    assert [g.rule for g in drift.overrides] == ["Bash(bin/get-secret:*)"]
    assert drift.covered == []


def test_a_local_wildcard_reaching_a_gated_script_is_an_override(tmp_path):
    """The wildcard matches `bin/get-secret` itself, so the ask rule is bypassed."""
    assert [r for r, _ in _overrides(tmp_path, "Bash(bin/get-*)")] == ["Bash(bin/get-*)"]


def test_a_grant_beside_a_gated_script_is_not_an_override(tmp_path):
    """A sibling script the ask rule's prefix does not spell is nobody's gate."""
    assert _overrides(tmp_path, "Bash(bin/local/validate-all)", "Bash(sh /tmp/probe.sh)") == []


def test_the_ask_prefix_reaches_every_command_it_spells(tmp_path):
    """`Bash(bin/get-secret:*)` is startsWith, so it gates `bin/get-secrets-report`
    as well — the local allow really would drop the prompt for it."""
    assert [r for r, _ in _overrides(tmp_path, "Bash(bin/get-secrets-report)")] == [
        "Bash(bin/get-secrets-report)"
    ]


def test_only_the_allow_bucket_drifts(tmp_path):
    """A local `ask` or `deny` narrows the tracked grant rather than duplicating it."""
    path = _local(tmp_path, "Bash(bin/local/validate-all)", bucket="ask")
    assert vp.drift_in(path, TRACKED).empty


def test_a_repeated_local_grant_is_reported_once(tmp_path):
    path = _local(tmp_path, "Bash(bin/x)", "Bash(bin/x)")
    assert len(vp.drift_in(path, TRACKED).covered) == 1


def test_a_covered_grant_carries_the_line_it_is_written_on(tmp_path):
    path = _local(tmp_path, "Bash(sh /tmp/probe.sh)", "Bash(bin/x)")
    assert [g.line for g in vp.drift_in(path, TRACKED).covered] == [5]


def test_a_repo_with_no_tracked_settings_file_makes_no_grants(tmp_path):
    assert vp.tracked_rules(str(tmp_path)) == vp.TrackedRules()


def test_the_tracked_rules_are_read_from_the_project_file():
    """The file owns the list — the granted directories are not hardcoded here."""
    tracked = vp.tracked_rules(str(REPO_ROOT))
    assert "bin/*" in tracked.allow
    assert "bin/get-secret:*" in tracked.ask


# ── container discovery ──────────────────────────────────────────────────


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _seed_repo(path):
    """A one-commit repo, with an identity so the commit does not need the user's."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", "-q", str(path)], check=True,
                   capture_output=True)
    _git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
         "--no-verify", "-m", "init")
    return path


@pytest.fixture
def container(tmp_path):
    """The bare-repo worktree layout: worktrees as peers of a bare `.git`."""
    seed = _seed_repo(tmp_path / "seed")
    root = tmp_path / "container"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(root / ".git")], check=True,
                   capture_output=True)
    _git(root / ".git", "worktree", "add", "-q", str(root / "main"), "main")
    return root


def test_a_normal_clone_has_no_container(tmp_path):
    """CI runs here: the shared git dir's parent is the checkout itself."""
    assert vp.container_dir(str(_seed_repo(tmp_path / "clone"))) is None


def test_a_directory_outside_git_has_no_container(tmp_path):
    assert vp.container_dir(str(tmp_path)) is None


def test_a_worktree_finds_its_bare_repo_container(container):
    found = vp.container_dir(str(container / "main"))
    assert found == str(Path(container).resolve())


def test_a_linked_worktree_of_a_normal_clone_has_no_container(tmp_path):
    """The parent there is somebody's checkout, and its .claude/ is tracked."""
    seed = _seed_repo(tmp_path / "seed")
    _git(seed, "worktree", "add", "-q", str(tmp_path / "feature"), "-b", "feature")
    assert vp.container_dir(str(tmp_path / "feature")) is None


def test_the_container_settings_file_is_discovered(container):
    claude = container / ".claude"
    claude.mkdir()
    (claude / vp.LOCAL_SETTINGS).write_text(json.dumps({"permissions": {"allow": ["Bash(x)"]}}))
    assert vp.discover_container_settings(str(container)) == [
        str(claude / vp.LOCAL_SETTINGS)
    ]


def test_a_container_with_no_claude_directory_contributes_nothing(container):
    assert vp.discover_container_settings(str(container)) == []


def test_no_container_contributes_nothing():
    assert vp.discover_container_settings(None) == []


def test_the_container_file_is_local_but_a_worktree_file_is_not(container):
    tracked = container / "main" / ".claude" / "settings.json"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("{}")
    local = container / ".claude" / vp.LOCAL_SETTINGS
    local.parent.mkdir()
    local.write_text("{}")

    assert vp._is_local(str(local), str(container))
    assert not vp._is_local(str(tracked), str(container))


# ── discovery and CLI ────────────────────────────────────────────────────


def test_discovery_finds_the_claude_template():
    found = vp.discover_settings(str(REPO_ROOT))
    assert str(REPO_ROOT / "ai" / "claude" / "settings.json") in found


def test_discovery_skips_settings_files_declaring_no_bash_rules():
    """editors/zed/settings.json is a settings.json with nothing to validate."""
    found = vp.discover_settings(str(REPO_ROOT))
    assert str(REPO_ROOT / "editors" / "zed" / "settings.json") not in found


def test_the_repo_declares_no_dead_rules():
    """The rule this validator enforces holds across every settings file it finds."""
    offenders = {path: vp.check_file(path) for path in vp.discover_settings(str(REPO_ROOT))}
    assert {p: v for p, v in offenders.items() if v} == {}


def test_main_exits_0_on_a_live_file(tmp_path, monkeypatch, capsys):
    path = _settings(tmp_path, {"permissions": {"allow": ["Bash(git:*)"]}})
    monkeypatch.setattr(sys, "argv", ["validate-permissions", str(path)])
    vp.main()
    assert "every permission rule is live" in capsys.readouterr().out


def test_main_exits_1_on_a_dead_rule(tmp_path, monkeypatch, capsys):
    path = _settings(tmp_path, {"permissions": {"allow": ["Bash(bin/local/*:*)"]}})
    monkeypatch.setattr(sys, "argv", ["validate-permissions", "--quiet", str(path)])
    with pytest.raises(SystemExit) as exc:
        vp.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Bash(bin/local/*:*)" in err
    assert "use Bash(bin/local/*)" in err


def test_main_warns_without_failing_on_a_covered_grant(tmp_path, monkeypatch, capsys):
    """The file is gitignored and regrows daily, so it cannot gate a push."""
    path = _local(tmp_path, "Bash(bin/local/validate-all)")
    monkeypatch.setattr(sys, "argv", ["validate-permissions", path])
    vp.main()
    captured = capsys.readouterr()
    assert "Bash(bin/local/validate-all)" in captured.err
    assert "delete the local grant" in captured.err
    assert "every permission rule is live" in captured.out


def test_main_stays_silent_about_a_covered_grant_under_quiet(tmp_path, monkeypatch, capsys):
    """validate-all captures a passing run's output and throws it away."""
    path = _local(tmp_path, "Bash(bin/local/validate-all)")
    monkeypatch.setattr(sys, "argv", ["validate-permissions", "--quiet", path])
    vp.main()
    assert "Bash(bin/local/validate-all)" not in capsys.readouterr().err


def test_main_exits_1_on_a_grant_that_re_grants_a_gated_script(tmp_path, monkeypatch, capsys):
    path = _local(tmp_path, "Bash(bin/get-secret --name prod/db)")
    monkeypatch.setattr(sys, "argv", ["validate-permissions", "--quiet", path])
    with pytest.raises(SystemExit) as exc:
        vp.main()
    assert exc.value.code == 1
    assert "Bash(bin/get-secret:*)" in capsys.readouterr().err


def test_a_tracked_settings_file_is_never_checked_for_drift(tmp_path, monkeypatch, capsys):
    """Only untracked files drift — the tracked file is where grants belong."""
    path = _settings(tmp_path, {"permissions": {"allow": ["Bash(bin/local/validate-all)"]}})
    monkeypatch.setattr(sys, "argv", ["validate-permissions", str(path)])
    vp.main()
    assert "already grants this" not in capsys.readouterr().err
