"""Tests for review_grouping: tier classification, file grouping, and profiles."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review.grouping import (
    GROUP_TIER1,
    GROUP_TIER3,
    MAX_GROUP_FILES,
    ReviewProfile,
    ReviewRule,
    _split_large_dir,
    classify_tier,
    format_profiles_section,
    group_files,
    load_profiles,
    match_profiles,
    merge_smallest_groups,
)
from gh.types import PRMetadata
from review.types import Group

try:
    import yaml as _yaml
except ImportError:
    _yaml = None

needs_yaml = pytest.mark.skipif(_yaml is None, reason="PyYAML not installed")


# ── classify_tier ────────────────────────────────────────────────────────────


class TestClassifyTier:
    def test_tier1_migrations(self):
        assert classify_tier("db/migrations/001_init.sql") == 1

    def test_tier1_proto(self):
        assert classify_tier("api/service.proto") == 1

    def test_tier1_claude_md(self):
        assert classify_tier("CLAUDE.md") == 1

    def test_tier1_go_mod(self):
        assert classify_tier("go.mod") == 1

    def test_tier1_auth_path(self):
        assert classify_tier("pkg/auth/handler.go") == 1

    def test_tier2_normal(self):
        assert classify_tier("pkg/service/handler.go") == 2

    def test_tier3_pb_go(self):
        assert classify_tier("api/service.pb.go") == 3

    def test_tier3_go_sum(self):
        assert classify_tier("go.sum") == 3

    def test_tier3_gen_path(self):
        assert classify_tier("gen/api/types.go") == 3

    def test_tier3_testdata_path(self):
        assert classify_tier("pkg/testdata/fixture.json") == 3

    def test_generated_wins_over_critical(self):
        assert classify_tier("pkg/auth/service.pb.go") == 3


# ── _split_large_dir ─────────────────────────────────────────────────────────


class TestSplitLargeDir:
    def test_fits_in_one_group(self):
        files = ["a.go", "b.go"]
        file_lines = {"a.go": 100, "b.go": 100}
        groups = _split_large_dir("pkg", files, file_lines)
        assert len(groups) == 1
        assert groups[0].name == "pkg-1"
        assert groups[0].files == files

    def test_requires_split(self):
        files = ["a.go", "b.go", "c.go"]
        file_lines = {"a.go": 500, "b.go": 500, "c.go": 500}
        groups = _split_large_dir("pkg", files, file_lines)
        assert len(groups) > 1
        all_files = [f for g in groups for f in g.files]
        assert set(all_files) == set(files)

    def test_splits_on_file_count(self):
        files = [f"f{i}.py" for i in range(20)]
        file_lines = {f: 10 for f in files}
        groups = _split_large_dir("pkg", files, file_lines)
        assert len(groups) > 1
        assert all(len(g.files) <= MAX_GROUP_FILES for g in groups)
        all_files = [f for g in groups for f in g.files]
        assert set(all_files) == set(files)


# ── group_files ──────────────────────────────────────────────────────────────


def _pr(files: list[dict]) -> PRMetadata:
    return PRMetadata(
        title="test", body="", head="feat", base="main", head_sha="abc",
        additions=sum(f["additions"] for f in files),
        deletions=sum(f["deletions"] for f in files),
        changed_files=len(files), files=files,
    )


class TestGroupFiles:
    def test_mix_of_tiers(self):
        pr = _pr([
            {"path": "CLAUDE.md", "additions": 10, "deletions": 5},
            {"path": "pkg/handler.go", "additions": 50, "deletions": 20},
            {"path": "go.sum", "additions": 40, "deletions": 25},
        ])
        names = [g.name for g in group_files(pr)]
        assert GROUP_TIER1 in names
        assert GROUP_TIER3 in names
        assert "pkg" in names

    def test_large_dir_split(self):
        pr = _pr([
            {"path": f"pkg/file{i}.go", "additions": 500, "deletions": 0}
            for i in range(5)
        ])
        # pkg dir has 2500 lines, so it must be split
        pkg_groups = [g for g in group_files(pr) if g.name.startswith("pkg")]
        assert len(pkg_groups) > 1

    def test_many_files_split(self):
        pr = _pr([
            {"path": f"pkg/file{i}.go", "additions": 10, "deletions": 5}
            for i in range(20)
        ])
        pkg_groups = [g for g in group_files(pr) if g.name.startswith("pkg")]
        assert len(pkg_groups) > 1
        assert all(len(g.files) <= MAX_GROUP_FILES for g in pkg_groups)
        all_files = [f for g in pkg_groups for f in g.files]
        assert set(all_files) == {f"pkg/file{i}.go" for i in range(20)}

    def test_exactly_max_files_no_split(self):
        # group_files uses > MAX_GROUP_FILES, so exactly MAX_GROUP_FILES files
        # must not trigger a split — verifies the threshold is exclusive
        pr = _pr([
            {"path": f"pkg/file{i}.go", "additions": 10, "deletions": 0}
            for i in range(MAX_GROUP_FILES)
        ])
        pkg_groups = [g for g in group_files(pr) if g.name.startswith("pkg")]
        assert len(pkg_groups) == 1


# ── merge_smallest_groups ────────────────────────────────────────────────────


class TestMergeSmallestGroups:
    def test_under_limit(self):
        groups = [Group("a", ["f1"], 10), Group("b", ["f2"], 20)]
        assert len(merge_smallest_groups(groups, 5)) == 2

    def test_over_limit(self):
        groups = [
            Group("a", ["f1"], 10),
            Group("b", ["f2"], 20),
            Group("c", ["f3"], 30),
        ]
        assert len(merge_smallest_groups(groups, 2)) == 2

    def test_merged_name_and_files(self):
        groups = [Group("a", ["f1"], 10), Group("b", ["f2"], 20)]
        result = merge_smallest_groups(groups, 1)
        assert len(result) == 1
        assert "a" in result[0].name
        assert "b" in result[0].name
        assert set(result[0].files) == {"f1", "f2"}
        assert result[0].lines == 30

    def test_prefers_shared_directory_prefix(self):
        groups = [
            Group("src/api", ["api.go"], 100),
            Group("src/auth", ["auth.go"], 100),
            Group("tests/unit", ["test.go"], 50),
        ]
        result = merge_smallest_groups(groups, 2)
        assert len(result) == 2
        merged = [g for g in result if "+" in g.name][0]
        assert "src/api" in merged.name
        assert "src/auth" in merged.name

    def test_falls_back_to_size_without_shared_prefix(self):
        groups = [
            Group("alpha", ["a.go"], 500),
            Group("beta", ["b.go"], 10),
            Group("gamma", ["c.go"], 20),
        ]
        result = merge_smallest_groups(groups, 2)
        assert len(result) == 2
        merged = [g for g in result if "+" in g.name][0]
        assert "beta" in merged.name
        assert "gamma" in merged.name


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

    def test_narrows_to_one_groups_files(self):
        profiles = [
            ReviewProfile("auth", "", paths=["auth/**"], rules=[]),
            ReviewProfile("db", "", paths=["db/**"], rules=[]),
        ]
        matched = match_profiles(profiles, ["auth/login.py"])
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
