"""Tests for bin/local/validate-permissions."""

import importlib.machinery
import importlib.util
import json
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
        "_generated_permissions": ["Bash(d/*:*)"],
    })
    assert [v.rule for v in vp.check_file(str(path))] == [
        "Bash(a/*:*)", "Bash(b/*:*)", "Bash(c/*:*)", "Bash(d/*:*)"
    ]


def test_a_rule_repeated_across_buckets_is_reported_once(tmp_path):
    path = _settings(tmp_path, {
        "permissions": {"allow": ["Bash(a/*:*)"]},
        "_generated_permissions": ["Bash(a/*:*)"],
    })
    assert len(vp.check_file(str(path))) == 1


def test_violations_carry_the_line_they_are_written_on(tmp_path):
    path = _settings(tmp_path, {"permissions": {"allow": ["Bash(git:*)", "Bash(a/*:*)"]}})
    assert [v.line for v in vp.check_file(str(path))] == [5]


def test_a_settings_file_with_no_permissions_block_is_clean(tmp_path):
    path = _settings(tmp_path, {"statusLine": {"command": "x"}})
    assert vp.check_file(str(path)) == []


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
