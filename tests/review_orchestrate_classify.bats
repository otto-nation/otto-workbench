#!/usr/bin/env bats

setup_file() {
  load 'test_helper'
  # warm .pyc cache; errors caught at import
  python3 -m compileall -q "$REPO_ROOT/ai/claude/lib" "$REPO_ROOT/ai/claude/bin" 2>/dev/null || true
  export ORCHESTRATE="$REPO_ROOT/ai/claude/bin/review-orchestrate"
}

setup() {
  load 'test_helper'
  load 'review_orchestrate_helper'
  common_setup
  TMPDIR="$BATS_TEST_TMPDIR"
}

teardown() {
  common_teardown
}

@test "review-orchestrate --help exits 0" {
  run "$ORCHESTRATE" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Review orchestration"* ]]
}

@test "review-orchestrate missing required args exits non-zero" {
  run "$ORCHESTRATE" --pr 42
  [ "$status" -ne 0 ]
}

# ── Tier classification ──────────────────────────────────────────────────────

@test "classify_tier: CLAUDE.md is tier 1" {
  result=$(_py 'print(mod.classify_tier("CLAUDE.md"))')
  [ "$result" = "1" ]
}

@test "classify_tier: go.mod is tier 1" {
  result=$(_py 'print(mod.classify_tier("go.mod"))')
  [ "$result" = "1" ]
}

@test "classify_tier: auth path is tier 1" {
  result=$(_py 'print(mod.classify_tier("pkg/auth/handler.go"))')
  [ "$result" = "1" ]
}

@test "classify_tier: migrations path is tier 1" {
  result=$(_py 'print(mod.classify_tier("db/migrations/001_init.sql"))')
  [ "$result" = "1" ]
}

@test "classify_tier: proto file is tier 1" {
  result=$(_py 'print(mod.classify_tier("api/service.proto"))')
  [ "$result" = "1" ]
}

@test "classify_tier: regular go file is tier 2" {
  result=$(_py 'print(mod.classify_tier("pkg/service/handler.go"))')
  [ "$result" = "2" ]
}

@test "classify_tier: gen path is tier 3" {
  result=$(_py 'print(mod.classify_tier("pkg/gen/models.go"))')
  [ "$result" = "3" ]
}

@test "classify_tier: pb.go file is tier 3" {
  result=$(_py 'print(mod.classify_tier("api/service.pb.go"))')
  [ "$result" = "3" ]
}

@test "classify_tier: go.sum is tier 3" {
  result=$(_py 'print(mod.classify_tier("go.sum"))')
  [ "$result" = "3" ]
}

@test "classify_tier: testdata is tier 3" {
  result=$(_py 'print(mod.classify_tier("pkg/testdata/fixture.json"))')
  [ "$result" = "3" ]
}

# ── File grouping ─────────────────────────────────────────────────────────────

@test "group_files: separates tiers" {
  result=$(_py '
pr = mod.PRMetadata(
    title="test", body="", head="feat", base="main", head_sha="abc",
    additions=100, deletions=50, changed_files=3,
    files=[
        {"path": "CLAUDE.md", "additions": 10, "deletions": 5},
        {"path": "pkg/handler.go", "additions": 40, "deletions": 20},
        {"path": "pkg/gen/models.go", "additions": 50, "deletions": 25},
    ],
)
groups = mod.group_files(pr)
names = [g.name for g in groups]
print(",".join(names))
')
  [[ "$result" == *"tier1-critical"* ]]
  [[ "$result" == *"pkg"* ]]
  [[ "$result" == *"tier3-generated"* ]]
}

@test "group_files: splits large directories" {
  result=$(_py '
files = [{"path": f"pkg/file{i}.go", "additions": 500, "deletions": 0} for i in range(3)]
pr = mod.PRMetadata(
    title="test", body="", head="feat", base="main", head_sha="abc",
    additions=1500, deletions=0, changed_files=3, files=files,
)
groups = mod.group_files(pr)
names = [g.name for g in groups]
print(",".join(names))
')
  [[ "$result" == *"pkg-1"* ]]
  [[ "$result" == *"pkg-2"* ]]
}

# ── Group merging ────────────────────────────────────────────────────────────

@test "_merge_smallest_groups: reduces count to max" {
  result=$(_py '
groups = [
    mod.Group("a", ["a.go"], 100),
    mod.Group("b", ["b.go"], 200),
    mod.Group("c", ["c.go"], 50),
    mod.Group("d", ["d.go"], 150),
    mod.Group("e", ["e.go"], 75),
]
merged = mod._merge_smallest_groups(groups, 3)
print(len(merged))
')
  [ "$result" = "3" ]
}

@test "_merge_smallest_groups: merges smallest groups first" {
  result=$(_py '
groups = [
    mod.Group("big", ["big.go"], 500),
    mod.Group("small1", ["s1.go"], 10),
    mod.Group("small2", ["s2.go"], 20),
    mod.Group("medium", ["m.go"], 100),
]
merged = mod._merge_smallest_groups(groups, 3)
names = [g.name for g in merged]
lines = [g.lines for g in merged]
print(sorted(names))
print(sorted(lines))
')
  # small1 (10) + small2 (20) should be merged into one group (30 lines)
  [[ "$result" == *"30"* ]]
  [[ "$result" == *"500"* ]]
  [[ "$result" == *"100"* ]]
}

@test "_merge_smallest_groups: noop when under cap" {
  result=$(_py '
groups = [
    mod.Group("a", ["a.go"], 100),
    mod.Group("b", ["b.go"], 200),
]
merged = mod._merge_smallest_groups(groups, 5)
print(len(merged))
')
  [ "$result" = "2" ]
}

