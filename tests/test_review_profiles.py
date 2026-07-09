"""Tests for review_profiles: profile loading, matching, and formatting."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_profiles import (
    ReviewProfile,
    ReviewRule,
    format_profiles_section,
    load_profiles,
    match_profiles,
    match_profiles_for_group,
)

try:
    import yaml as _yaml
except ImportError:
    _yaml = None

needs_yaml = pytest.mark.skipif(_yaml is None, reason="PyYAML not installed")


# ── load_profiles ────────────────────────────────────────────────────────────


AUTH_PROFILE_YAML = """\
name: authentication
description: Auth service review rules
paths:
  - "auth/**"
  - "middleware/auth*"
rules:
  - severity: must-fix
    rule: "All authentication decisions must check token expiry"
    evidence: "Incident 2024-03: expired tokens accepted for 2h"
  - severity: should-fix
    rule: "Use centralized auth middleware"
"""

DB_PROFILE_YAML = """\
name: database
description: Database access patterns
paths:
  - "db/**"
  - "models/**"
rules:
  - severity: must-fix
    rule: "Always use parameterized queries"
"""


@needs_yaml
class TestLoadProfiles:
    def test_loads_from_directory(self, tmp_path):
        profiles_dir = tmp_path / ".claude" / "review" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "auth.yml").write_text(AUTH_PROFILE_YAML)
        (profiles_dir / "db.yml").write_text(DB_PROFILE_YAML)

        profiles = load_profiles(str(tmp_path))
        assert len(profiles) == 2
        names = {p.name for p in profiles}
        assert "authentication" in names
        assert "database" in names

    def test_parses_rules(self, tmp_path):
        profiles_dir = tmp_path / ".claude" / "review" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "auth.yml").write_text(AUTH_PROFILE_YAML)

        profiles = load_profiles(str(tmp_path))
        assert len(profiles[0].rules) == 2
        assert profiles[0].rules[0].severity == "must-fix"
        assert "token expiry" in profiles[0].rules[0].rule
        assert "Incident" in profiles[0].rules[0].evidence

    def test_missing_directory(self, tmp_path):
        assert load_profiles(str(tmp_path)) == []

    def test_skips_malformed_yaml(self, tmp_path):
        profiles_dir = tmp_path / ".claude" / "review" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "bad.yml").write_text("not: [valid: yaml: {{")
        (profiles_dir / "good.yml").write_text(DB_PROFILE_YAML)

        profiles = load_profiles(str(tmp_path))
        assert len(profiles) == 1
        assert profiles[0].name == "database"

    def test_skips_non_dict_yaml(self, tmp_path):
        profiles_dir = tmp_path / ".claude" / "review" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "list.yml").write_text("- item1\n- item2\n")

        assert load_profiles(str(tmp_path)) == []

    def test_default_name_from_filename(self, tmp_path):
        profiles_dir = tmp_path / ".claude" / "review" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "api.yml").write_text("description: API rules\nrules: []\n")

        profiles = load_profiles(str(tmp_path))
        assert profiles[0].name == "api"

    def test_default_severity(self, tmp_path):
        profiles_dir = tmp_path / ".claude" / "review" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "x.yml").write_text(
            "name: x\nrules:\n  - rule: 'no severity specified'\n"
        )

        profiles = load_profiles(str(tmp_path))
        assert profiles[0].rules[0].severity == "should-fix"

    def test_skips_rules_without_rule_key(self, tmp_path):
        profiles_dir = tmp_path / ".claude" / "review" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "x.yml").write_text(
            "name: x\nrules:\n  - severity: must-fix\n  - rule: 'valid'\n"
        )

        profiles = load_profiles(str(tmp_path))
        assert len(profiles[0].rules) == 1
        assert profiles[0].rules[0].rule == "valid"

    def test_sorted_by_filename(self, tmp_path):
        profiles_dir = tmp_path / ".claude" / "review" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "z.yml").write_text("name: z\nrules: []\n")
        (profiles_dir / "a.yml").write_text("name: a\nrules: []\n")

        profiles = load_profiles(str(tmp_path))
        assert profiles[0].name == "a"
        assert profiles[1].name == "z"


# ── match_profiles ───────────────────────────────────────────────────────────


class TestMatchProfiles:
    def _make_profiles(self):
        return [
            ReviewProfile("auth", "Auth rules", paths=["auth/**", "middleware/auth*"], rules=[]),
            ReviewProfile("db", "DB rules", paths=["db/**", "models/**"], rules=[]),
            ReviewProfile("global", "Global rules", paths=[], rules=[]),
        ]

    def test_matches_by_path(self):
        profiles = self._make_profiles()
        matched = match_profiles(profiles, ["auth/handler.py", "README.md"])
        names = {p.name for p in matched}
        assert "auth" in names
        assert "global" in names
        assert "db" not in names

    def test_no_paths_always_matches(self):
        profiles = self._make_profiles()
        matched = match_profiles(profiles, ["unrelated.txt"])
        names = {p.name for p in matched}
        assert "global" in names

    def test_no_files_only_global(self):
        profiles = self._make_profiles()
        matched = match_profiles(profiles, [])
        assert len(matched) == 1
        assert matched[0].name == "global"

    def test_multiple_matches(self):
        profiles = self._make_profiles()
        matched = match_profiles(profiles, ["auth/x.py", "db/query.py"])
        names = {p.name for p in matched}
        assert "auth" in names
        assert "db" in names
        assert "global" in names

    def test_no_match(self):
        profiles = [
            ReviewProfile("auth", "", paths=["auth/**"], rules=[]),
        ]
        matched = match_profiles(profiles, ["frontend/app.js"])
        assert matched == []


# ── match_profiles_for_group ─────────────────────────────────────────────────


class TestMatchProfilesForGroup:
    def test_filters_to_group_files(self):
        profiles = [
            ReviewProfile("auth", "", paths=["auth/**"], rules=[]),
            ReviewProfile("db", "", paths=["db/**"], rules=[]),
        ]
        matched = match_profiles_for_group(profiles, ["auth/login.py"])
        assert len(matched) == 1
        assert matched[0].name == "auth"


# ── format_profiles_section ──────────────────────────────────────────────────


class TestFormatProfilesSection:
    def test_formats_with_rules(self):
        profiles = [
            ReviewProfile("auth", "Auth rules", paths=[], rules=[
                ReviewRule("must-fix", "Check token expiry", "Incident 2024-03"),
                ReviewRule("should-fix", "Use middleware"),
            ]),
        ]
        output = format_profiles_section(profiles)
        assert "#### Review profiles" in output
        assert "**auth**" in output
        assert "[must-fix] Check token expiry" in output
        assert "Evidence: Incident 2024-03" in output
        assert "[should-fix] Use middleware" in output

    def test_sorts_rules_by_severity(self):
        profiles = [
            ReviewProfile("x", "", paths=[], rules=[
                ReviewRule("nit", "nit rule"),
                ReviewRule("must-fix", "must rule"),
                ReviewRule("should-fix", "should rule"),
            ]),
        ]
        output = format_profiles_section(profiles)
        lines = output.splitlines()
        rule_lines = [l for l in lines if l.startswith("- [")]
        assert "must-fix" in rule_lines[0]
        assert "should-fix" in rule_lines[1]
        assert "nit" in rule_lines[2]

    def test_empty_profiles(self):
        assert format_profiles_section([]) == ""

    def test_multiple_profiles(self):
        profiles = [
            ReviewProfile("auth", "Auth", paths=[], rules=[
                ReviewRule("must-fix", "rule a"),
            ]),
            ReviewProfile("db", "DB", paths=[], rules=[
                ReviewRule("should-fix", "rule b"),
            ]),
        ]
        output = format_profiles_section(profiles)
        assert "**auth**" in output
        assert "**db**" in output

    def test_profile_with_description(self):
        profiles = [
            ReviewProfile("auth", "Auth service review rules", paths=[], rules=[]),
        ]
        output = format_profiles_section(profiles)
        assert "Auth service review rules" in output
