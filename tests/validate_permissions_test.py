"""Tests for bin/local/validate-permissions.

The validator's matcher, coverage test, and discovery walk live in
``lib/permissions.py``, which it shares with ``lib/permission_sweep.py``. It
imports the ones it uses into its own namespace, so ``vp.drift_in`` and friends
resolve and the assertions below read as the validator's behaviour. ``perms``
is here for the handful the validator has no call for — a name it imported only
to be tested through would be an unused import standing in for a test seam.
"""

import json
import sys
from pathlib import Path

import pytest
from conftest import git_in, load_script, seed_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-permissions"

sys.path.insert(0, str(REPO_ROOT / "lib"))
import permissions as perms  # noqa: E402

vp = load_script("validate_permissions", SCRIPT)


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


# ── allow ordering ───────────────────────────────────────────────────────
# Claude Code rewrites `allow` in codepoint order whenever a session grants a
# permission. A committed file written any other way comes back modified with
# nothing added and nothing removed, and that diff then reads as uncommitted
# work in every worktree holding it.


def _misordered(*allow):
    return perms.first_misordered(list(allow))


def test_an_allow_in_the_writers_order_is_clean():
    assert _misordered("Bash(awk:*)", "Bash(git:*)", "Bash(xargs:*)") is None


def test_an_empty_allow_is_clean():
    assert _misordered() is None


def test_the_first_rule_out_of_order_is_named():
    assert _misordered("Bash(git:*)", "Bash(awk:*)") == "Bash(git:*)"


def test_the_writers_order_is_codepoint_not_alphabetical():
    """`~` sorts after every letter, which is where the writer puts it — and
    where the entry that churned seven worktrees was not."""
    assert _misordered("Bash(xargs:*)", "Bash(~/bin/x)", "Edit") is None
    assert _misordered("Bash(~/bin/x)", "Bash(xargs:*)") == "Bash(~/bin/x)"


def test_only_the_allow_bucket_is_ordered(tmp_path):
    """The writer leaves `deny` alone — both were seen in one save — and it is
    grouped here by the command it guards."""
    path = _settings(tmp_path, {"permissions": {
        "allow": ["Bash(awk:*)"],
        "deny": ["Bash(git reset:*)", "Bash(git push --force:*)", "Bash(git push -f:*)"],
    }})
    assert vp.check_order(str(path)) is None


def test_a_settings_file_with_no_permissions_block_is_in_order(tmp_path):
    assert vp.check_order(str(_settings(tmp_path, {"statusLine": {"command": "x"}}))) is None


def test_a_misordered_rule_carries_the_line_it_is_written_on(tmp_path):
    path = _settings(tmp_path, {"permissions": {"allow": ["Bash(git:*)", "Bash(awk:*)"]}})
    assert vp.check_order(str(path)) == vp.Misordered("Bash(git:*)", 4)


def test_the_repo_commits_no_misordered_allow():
    """The rule this validator enforces holds across every tracked settings file.

    Both of them are symlinked or synced into place for Claude Code to write
    back to, so either one drifting reproduces the churn.
    """
    tracked = [p for p in vp.discover_settings(str(REPO_ROOT)) if not perms.is_local(p, False)]
    assert tracked, "expected at least the machine-wide and project settings files"
    found = {path: vp.check_order(path) for path in tracked}
    assert {p: m for p, m in found.items() if m} == {}


def test_main_exits_1_on_a_misordered_allow(tmp_path, monkeypatch, capsys):
    path = _settings(tmp_path, {"permissions": {"allow": ["Bash(git:*)", "Bash(awk:*)"]}})
    monkeypatch.setattr(sys, "argv", ["validate-permissions", "--quiet", str(path)])
    with pytest.raises(SystemExit) as exc:
        vp.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Bash(git:*)" in err
    assert vp.FIX_COMMAND in err


def test_a_local_file_is_not_judged_on_order(tmp_path, monkeypatch, capsys):
    """It is Claude Code's own file and dies with the checkout, so its order
    churns no diff."""
    path = _local(tmp_path, "Bash(git:*)", "Bash(awk:*)")
    monkeypatch.setattr(sys, "argv", ["validate-permissions", path])
    vp.main()
    assert "not in the order" not in capsys.readouterr().err


def test_the_container_mirror_is_not_judged_on_order(container, monkeypatch, capsys):
    """`permissions mirror` puts the managed rules in front of the ones already
    there on purpose; sorting would undo the contract that generates it."""
    worktree = container / "main"
    (worktree / ".claude").mkdir(parents=True)
    (worktree / vp.TRACKED_SETTINGS).write_text(json.dumps({"permissions": {"allow": []}}))
    _container_settings(container, "settings.json", {
        "permissions": {"allow": ["Bash(git:*)", "Bash(awk:*)"]},
        perms.MANIFEST_KEY: {"permissions": {"allow": ["Bash(git:*)"]}},
    })

    monkeypatch.setattr(vp, "_WORKBENCH_DIR", str(worktree))
    monkeypatch.setattr(sys, "argv", ["validate-permissions"])
    vp.main()

    assert "not in the order" not in capsys.readouterr().err


def test_fix_sorts_a_misordered_allow(tmp_path, monkeypatch, capsys):
    path = _settings(tmp_path, {"permissions": {"allow": ["Bash(git:*)", "Bash(awk:*)"]}})
    monkeypatch.setattr(sys, "argv", ["validate-permissions", "--fix", str(path)])
    vp.main()
    assert json.loads(path.read_text())["permissions"]["allow"] == [
        "Bash(awk:*)", "Bash(git:*)"
    ]


def test_sorting_grants_and_revokes_nothing(tmp_path, monkeypatch, capsys):
    """The no-op proof: a reorder is the same multiset of rules."""
    allow = ["Bash(git:*)", "Bash(awk:*)", "Read(~/Library/x/**)", "Bash(xargs:*)"]
    path = _settings(tmp_path, {"permissions": {"allow": allow}})
    monkeypatch.setattr(sys, "argv", ["validate-permissions", "--fix", "--quiet", str(path)])
    vp.main()
    assert sorted(json.loads(path.read_text())["permissions"]["allow"]) == sorted(allow)


def test_fix_leaves_the_other_buckets_and_keys_alone(tmp_path, monkeypatch, capsys):
    path = _settings(tmp_path, {
        "statusLine": {"command": "x"},
        "permissions": {
            "allow": ["Bash(git:*)", "Bash(awk:*)"],
            "deny": ["Bash(git reset:*)", "Bash(git push --force:*)"],
            "ask": ["Bash(bin/get-secret:*)"],
        },
    })
    monkeypatch.setattr(sys, "argv", ["validate-permissions", "--fix", "--quiet", str(path)])
    vp.main()

    fixed = json.loads(path.read_text())
    assert fixed["statusLine"] == {"command": "x"}
    assert fixed["permissions"]["deny"] == ["Bash(git reset:*)", "Bash(git push --force:*)"]
    assert fixed["permissions"]["ask"] == ["Bash(bin/get-secret:*)"]


def test_a_sorted_file_passes_on_the_next_run(tmp_path, monkeypatch, capsys):
    path = _settings(tmp_path, {"permissions": {"allow": ["Bash(git:*)", "Bash(awk:*)"]}})
    monkeypatch.setattr(sys, "argv", ["validate-permissions", "--fix", str(path)])
    vp.main()

    monkeypatch.setattr(sys, "argv", ["validate-permissions", str(path)])
    vp.main()
    assert "every permission rule is live" in capsys.readouterr().out


def test_fix_is_idempotent_on_order(tmp_path, monkeypatch, capsys):
    path = _settings(tmp_path, {"permissions": {"allow": ["Bash(git:*)", "Bash(awk:*)"]}})
    monkeypatch.setattr(sys, "argv", ["validate-permissions", "--fix", "--quiet", str(path)])
    vp.main()
    once = path.read_text()
    vp.main()
    assert path.read_text() == once


# ── local grant drift ────────────────────────────────────────────────────
# The tracked project file grants three bin directories and gates two scripts
# that reach credentials; these stand in for it so the tests do not move when
# the real file gains a directory.

TRACKED = perms.TrackedRules(allow=["bin/*", "ai/bin/*"], ask=["bin/get-secret:*"])

# A grant `TRACKED` already covers via `Bash(bin/*)` — shared across the tests
# below so they read as one known-covered example rather than a fresh literal.
COVERED_GRANT = "Bash(bin/local/validate-all)"


def _local(tmp_path, *rules, bucket="allow"):
    path = tmp_path / perms.LOCAL_SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"permissions": {bucket: list(rules)}}, indent=2))
    return str(path)


def _covered(tmp_path, *allow):
    return [(g.rule, g.tracked) for g in vp.drift_in(_local(tmp_path, *allow), TRACKED).covered]


def _overrides(tmp_path, *allow):
    return [(g.rule, g.tracked) for g in vp.drift_in(_local(tmp_path, *allow), TRACKED).overrides]


def test_a_grant_under_a_granted_directory_is_covered(tmp_path):
    assert _covered(tmp_path, COVERED_GRANT) == [
        (COVERED_GRANT, "Bash(bin/*)")
    ]


def test_a_grant_carrying_arguments_is_covered(tmp_path):
    """The tracked wildcard ends in `*`, so the whole invocation is inside it."""
    assert _covered(tmp_path, "Bash(ai/bin/pr --tool-schema)") == [
        ("Bash(ai/bin/pr --tool-schema)", "Bash(ai/bin/*)")
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
    tracked = perms.TrackedRules(allow=["env"])
    assert vp.drift_in(_local(tmp_path, "Bash(env:*)"), tracked).covered == []


def test_an_exactly_repeated_tracked_rule_is_covered(tmp_path):
    tracked = perms.TrackedRules(allow=["env"])
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
    assert _overrides(tmp_path, COVERED_GRANT, "Bash(sh /tmp/probe.sh)") == []


def test_the_ask_prefix_reaches_every_command_it_spells(tmp_path):
    """`Bash(bin/get-secret:*)` is startsWith, so it gates `bin/get-secrets-report`
    as well — the local allow really would drop the prompt for it."""
    assert [r for r, _ in _overrides(tmp_path, "Bash(bin/get-secrets-report)")] == [
        "Bash(bin/get-secrets-report)"
    ]


def _both_buckets(tmp_path, allow, ask):
    path = tmp_path / perms.LOCAL_SETTINGS
    path.write_text(json.dumps({"permissions": {"allow": [allow], "ask": [ask]}}, indent=2))
    return vp.drift_in(str(path), TRACKED)


def test_a_file_declaring_the_gate_itself_is_not_an_override(tmp_path):
    """`ask` outranks `allow`, so a file carrying both has kept the gate."""
    drift = _both_buckets(tmp_path, "Bash(bin/get-secret --name prod/db)",
                          "Bash(bin/get-secret:*)")
    assert drift.overrides == []


def test_the_kept_gate_leaves_the_grant_merely_covered(tmp_path):
    """`Bash(bin/*)` still grants it, so deleting it changes nothing."""
    drift = _both_buckets(tmp_path, "Bash(bin/get-secret --name prod/db)",
                          "Bash(bin/get-secret:*)")
    assert [g.tracked for g in drift.covered] == ["Bash(bin/*)"]


def test_a_narrower_local_ask_does_not_excuse_the_override(tmp_path):
    """It gates one invocation; the tracked prefix gates every command it spells,
    `bin/get-secrets-report` among them."""
    drift = _both_buckets(tmp_path, "Bash(bin/get-*)",
                          "Bash(bin/get-secret --name prod/db)")
    assert [g.rule for g in drift.overrides] == ["Bash(bin/get-*)"]


def test_only_the_allow_bucket_drifts(tmp_path):
    """A local `ask` or `deny` narrows the tracked grant rather than duplicating it."""
    path = _local(tmp_path, COVERED_GRANT, bucket="ask")
    assert vp.drift_in(path, TRACKED).empty


def test_a_repeated_local_grant_is_reported_once(tmp_path):
    path = _local(tmp_path, "Bash(bin/x)", "Bash(bin/x)")
    assert len(vp.drift_in(path, TRACKED).covered) == 1


def test_a_covered_grant_carries_the_line_it_is_written_on(tmp_path):
    path = _local(tmp_path, "Bash(sh /tmp/probe.sh)", "Bash(bin/x)")
    assert [g.line for g in vp.drift_in(path, TRACKED).covered] == [5]


def test_a_repo_with_no_tracked_settings_file_makes_no_grants(tmp_path):
    assert vp.tracked_rules(str(tmp_path)) == perms.TrackedRules()


def test_the_tracked_rules_are_read_from_the_project_file():
    """The file owns the list — the granted directories are not hardcoded here."""
    tracked = vp.tracked_rules(str(REPO_ROOT))
    assert "bin/*" in tracked.allow
    assert "bin/get-secret:*" in tracked.ask


# ── container discovery ──────────────────────────────────────────────────


def test_a_normal_clone_has_no_container(tmp_path):
    """CI runs here: the shared git dir's parent is the checkout itself."""
    assert vp.container_dir(str(seed_repo(tmp_path / "clone"))) is None


def test_a_directory_outside_git_has_no_container(tmp_path):
    assert vp.container_dir(str(tmp_path)) is None


def test_a_worktree_finds_its_bare_repo_container(container):
    found = vp.container_dir(str(container / "main"))
    assert found == str(Path(container).resolve())


def test_a_worktree_finds_its_container_under_an_inherited_git_dir(container, monkeypatch):
    """The pre-push hook exports GIT_DIR, and git reads it ahead of `-C`: with
    one set, `rev-parse --show-toplevel` asked at the container answers the
    container, the no-working-tree guard holds, and the container is skipped in
    exactly the run that had to see it."""
    monkeypatch.setenv("GIT_DIR", str(container / "main" / ".git"))
    assert vp.container_dir(str(container / "main")) == str(Path(container).resolve())


def test_a_linked_worktree_of_a_normal_clone_has_no_container(tmp_path):
    """The parent there is somebody's checkout, and its .claude/ is tracked."""
    seed = seed_repo(tmp_path / "seed")
    git_in(seed, "worktree", "add", "-q", str(tmp_path / "feature"), "-b", "feature")
    assert vp.container_dir(str(tmp_path / "feature")) is None


def test_the_container_settings_file_is_discovered(container):
    claude = container / ".claude"
    claude.mkdir()
    (claude / perms.LOCAL_SETTINGS).write_text(json.dumps({"permissions": {"allow": ["Bash(x)"]}}))
    assert vp.discover_container_settings(str(container)) == [
        str(claude / perms.LOCAL_SETTINGS)
    ]


def test_a_container_with_no_claude_directory_contributes_nothing(container):
    assert vp.discover_container_settings(str(container)) == []


def test_no_container_contributes_nothing():
    assert vp.discover_container_settings(None) == []


def test_the_container_file_is_local_but_a_worktree_file_is_not(container):
    tracked = container / "main" / ".claude" / "settings.json"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("{}")
    local = container / ".claude" / perms.LOCAL_SETTINGS
    local.parent.mkdir()
    local.write_text("{}")

    assert perms.is_local(str(local), perms.at_container(str(local), str(container)))
    assert not perms.is_local(str(tracked), perms.at_container(str(tracked), str(container)))


def _container_settings(container, name, body):
    path = container / ".claude" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(body, indent=2))
    return path


def test_a_generated_container_file_is_not_local(container):
    """Its rules were reviewed in the tracked file they were copied from."""
    path = _container_settings(container, "settings.json", {
        "permissions": {"allow": ["Bash(bin/*)"]},
        perms.MANIFEST_KEY: {"permissions": {"allow": ["Bash(bin/*)"]}},
    })
    assert not perms.is_local(str(path), perms.at_container(str(path), str(container)))


def test_a_hand_written_container_file_is_still_local(container):
    """No stamp, no tracked owner — this is the grant #871 exists to catch."""
    path = _container_settings(container, "settings.json",
                               {"permissions": {"allow": ["Bash(bin/x)"]}})
    assert perms.is_local(str(path), perms.at_container(str(path), str(container)))


def test_a_stamp_does_not_excuse_settings_local_json(container):
    """Claude Code owns that filename and appends to it; the stamp is not its."""
    path = _container_settings(container, perms.LOCAL_SETTINGS, {
        "permissions": {"allow": ["Bash(bin/x)"]},
        perms.MANIFEST_KEY: {"permissions": {"allow": ["Bash(bin/x)"]}},
    })
    assert perms.is_local(str(path), perms.at_container(str(path), str(container)))


def test_an_unparsable_container_file_stays_in_the_checked_class(container):
    path = container / ".claude" / "settings.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text("{ not json")
    assert perms.is_local(str(path), perms.at_container(str(path), str(container)))


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


def test_main_exits_1_on_a_covered_grant(tmp_path, monkeypatch, capsys):
    """A warning could not reach anyone: validate-all discards a green run's output."""
    path = _local(tmp_path, COVERED_GRANT)
    monkeypatch.setattr(sys, "argv", ["validate-permissions", path])
    with pytest.raises(SystemExit) as exc:
        vp.main()
    assert exc.value.code == 1
    assert COVERED_GRANT in capsys.readouterr().err


def test_the_failure_names_the_command_that_fixes_it(tmp_path, monkeypatch, capsys):
    """validate-all does not pass --fix, so the message has to spell it out."""
    path = _local(tmp_path, COVERED_GRANT)
    monkeypatch.setattr(sys, "argv", ["validate-permissions", "--quiet", path])
    with pytest.raises(SystemExit):
        vp.main()
    assert vp.FIX_COMMAND in capsys.readouterr().err


def test_main_exits_1_on_a_grant_that_re_grants_a_gated_script(tmp_path, monkeypatch, capsys):
    path = _local(tmp_path, "Bash(bin/get-secret --name prod/db)")
    monkeypatch.setattr(sys, "argv", ["validate-permissions", "--quiet", path])
    with pytest.raises(SystemExit) as exc:
        vp.main()
    assert exc.value.code == 1
    assert "Bash(bin/get-secret:*)" in capsys.readouterr().err


def test_main_reaches_the_container_file_above_the_worktree(container, monkeypatch, capsys):
    """End to end from a worktree: discovery, the drift check, and the advice.

    The grant is unreachable by any walk rooted in the worktree, and the fix it
    is given cannot be to edit a tracked file the container has no room for —
    the mirror is what carries the tracked rules there.
    """
    worktree = container / "main"
    (worktree / ".claude").mkdir(parents=True)
    (worktree / vp.TRACKED_SETTINGS).write_text(
        json.dumps({"permissions": {"allow": ["Bash(bin/*)"]}})
    )
    _local(container / ".claude", COVERED_GRANT)

    monkeypatch.setattr(vp, "_WORKBENCH_DIR", str(worktree))
    monkeypatch.setattr(sys, "argv", ["validate-permissions"])
    with pytest.raises(SystemExit):
        vp.main()

    err = capsys.readouterr().err
    assert COVERED_GRANT in err
    assert vp.MIRROR_COMMAND in err


def test_main_passes_a_generated_container_file(container, monkeypatch, capsys):
    """The gate has to accept the mirror, or pre-push fails on the fix itself.

    Every rule in it duplicates the tracked file — which is the point — so
    without the stamp each one reports as drift and the validator exits 1.
    """
    worktree = container / "main"
    (worktree / ".claude").mkdir(parents=True)
    (worktree / vp.TRACKED_SETTINGS).write_text(json.dumps({"permissions": {
        "allow": ["Bash(bin/*)"], "ask": ["Bash(bin/get-secret:*)"],
    }}))
    _container_settings(container, "settings.json", {
        "permissions": {"allow": ["Bash(bin/*)"], "ask": ["Bash(bin/get-secret:*)"]},
        perms.MANIFEST_KEY: {"permissions": {"allow": ["Bash(bin/*)"]}},
    })

    monkeypatch.setattr(vp, "_WORKBENCH_DIR", str(worktree))
    monkeypatch.setattr(sys, "argv", ["validate-permissions"])
    vp.main()

    assert "every permission rule is live" in capsys.readouterr().out


# ── --fix ────────────────────────────────────────────────────────────────


def _fix(path, monkeypatch, *flags):
    monkeypatch.setattr(sys, "argv", ["validate-permissions", "--fix", *flags, str(path)])
    vp.main()
    return json.loads(Path(path).read_text())


def test_fix_deletes_a_covered_grant(tmp_path, monkeypatch, capsys):
    path = _local(tmp_path, COVERED_GRANT, "Bash(sh /tmp/probe.sh)")
    assert _fix(path, monkeypatch)["permissions"]["allow"] == ["Bash(sh /tmp/probe.sh)"]


def test_fix_leaves_every_one_off_alone(tmp_path, monkeypatch, capsys):
    """Same discriminator as the check: no tracked home, no business being pruned."""
    one_offs = ["Bash(sh /tmp/probe.sh)", "Bash(uv run *)", "WebFetch(domain:example.com)"]
    path = _local(tmp_path, *one_offs)
    assert _fix(path, monkeypatch)["permissions"]["allow"] == one_offs


def test_fix_never_prunes_an_override(tmp_path, monkeypatch, capsys):
    """Deleting it would restore an ask gate on credentials — a human's call."""
    gated = "Bash(bin/get-secret --name prod/db)"
    path = _local(tmp_path, gated)
    monkeypatch.setattr(sys, "argv", ["validate-permissions", "--fix", str(path)])
    with pytest.raises(SystemExit) as exc:
        vp.main()
    assert exc.value.code == 1
    assert json.loads(Path(path).read_text())["permissions"]["allow"] == [gated]


def test_fix_removes_nothing_the_tracked_rules_do_not_already_grant(tmp_path, monkeypatch,
                                                                   capsys):
    """The no-op proof: every pruned rule's commands are matched by a tracked rule."""
    drifting = [COVERED_GRANT, "Bash(bin/local/compose-docs --check)",
                "Bash(ai/bin/pr --tool-schema)", "Bash(bin/local/validate-ceiling *)"]
    path = _local(tmp_path, *drifting, "Bash(sh /tmp/probe.sh)")
    before = set(json.loads(Path(path).read_text())["permissions"]["allow"])

    after = set(_fix(path, monkeypatch)["permissions"]["allow"])

    assert before - after == set(drifting)
    for rule in before - after:
        body = vp.BASH_RULE.match(rule).group(1)
        assert any(perms.covered_by(body, tracked) for tracked in TRACKED.allow)


def test_fix_preserves_the_rest_of_the_file(tmp_path, monkeypatch, capsys):
    path = tmp_path / perms.LOCAL_SETTINGS
    path.write_text(json.dumps({
        "statusLine": {"command": "x"},
        "permissions": {
            "allow": [COVERED_GRANT, "Bash(sh /tmp/probe.sh)"],
            "deny": ["Bash(rm -rf /)"],
            "ask": ["Bash(bin/x)"],
        },
    }, indent=2))

    fixed = _fix(path, monkeypatch)

    assert fixed["statusLine"] == {"command": "x"}
    assert fixed["permissions"]["deny"] == ["Bash(rm -rf /)"]
    assert fixed["permissions"]["ask"] == ["Bash(bin/x)"]
    assert fixed["permissions"]["allow"] == ["Bash(sh /tmp/probe.sh)"]


def test_fix_leaves_a_non_ascii_rule_written_the_way_it_was(tmp_path, monkeypatch, capsys):
    """Escaping an em dash to \\uXXXX would rewrite entries the run is not
    touching, turning a one-line deletion into a whole-file diff."""
    kept = "Bash(perl -pe 's/a — b/c/' f)"
    path = _local(tmp_path, COVERED_GRANT, kept)

    _fix(path, monkeypatch)

    assert "—" in Path(path).read_text()
    assert "\\u2014" not in Path(path).read_text()


def test_fix_keeps_an_emptied_allow_bucket(tmp_path, monkeypatch, capsys):
    """Claude Code appends to that list on the next approval; dropping the key
    would be a larger edit to a file this script does not own."""
    path = _local(tmp_path, COVERED_GRANT)
    assert _fix(path, monkeypatch)["permissions"]["allow"] == []


def test_fix_is_idempotent(tmp_path, monkeypatch, capsys):
    path = _local(tmp_path, COVERED_GRANT, "Bash(sh /tmp/probe.sh)")
    once = _fix(path, monkeypatch)
    first_text = Path(path).read_text()

    assert _fix(path, monkeypatch) == once
    assert Path(path).read_text() == first_text


def test_a_fixed_file_passes_on_the_next_run(tmp_path, monkeypatch, capsys):
    path = _local(tmp_path, COVERED_GRANT, "Bash(sh /tmp/probe.sh)")
    _fix(path, monkeypatch)

    monkeypatch.setattr(sys, "argv", ["validate-permissions", str(path)])
    vp.main()
    assert "every permission rule is live" in capsys.readouterr().out


def test_fix_never_writes_to_the_tracked_settings_file(tmp_path, monkeypatch, capsys):
    """Drift is only ever computed for untracked files, so pruning cannot reach it."""
    path = _settings(tmp_path, {"permissions": {"allow": [COVERED_GRANT]}})
    before = path.read_text()
    monkeypatch.setattr(sys, "argv", ["validate-permissions", "--fix", str(path)])
    vp.main()
    assert path.read_text() == before


def test_fix_prunes_the_container_file_above_the_worktree(container, monkeypatch, capsys):
    """The file the drift grows in is the one no worktree-rooted walk can reach."""
    worktree = container / "main"
    (worktree / ".claude").mkdir(parents=True)
    (worktree / vp.TRACKED_SETTINGS).write_text(
        json.dumps({"permissions": {"allow": ["Bash(bin/*)"]}})
    )
    local = Path(_local(container / ".claude", "Bash(bin/x)", "Bash(sh /tmp/probe.sh)"))

    monkeypatch.setattr(vp, "_WORKBENCH_DIR", str(worktree))
    monkeypatch.setattr(sys, "argv", ["validate-permissions", "--fix"])
    vp.main()

    assert json.loads(local.read_text())["permissions"]["allow"] == ["Bash(sh /tmp/probe.sh)"]
    assert "every permission rule is live" in capsys.readouterr().out


def test_a_tracked_settings_file_is_never_checked_for_drift(tmp_path, monkeypatch, capsys):
    """Only untracked files drift — the tracked file is where grants belong."""
    path = _settings(tmp_path, {"permissions": {"allow": [COVERED_GRANT]}})
    monkeypatch.setattr(sys, "argv", ["validate-permissions", str(path)])
    vp.main()
    assert "already grants this" not in capsys.readouterr().err
