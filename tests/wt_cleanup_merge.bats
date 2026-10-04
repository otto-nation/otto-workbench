#!/usr/bin/env bats
# Tests for wt-cleanup — merge detection, protection, age-based removal, dry-run and quiet modes.
bats_require_minimum_version 1.5.0

setup_file() {
  load 'test_helper'
  load 'wt_cleanup_helper'
  _wt_cleanup_mocks
}

setup() {
  load 'test_helper'
  load 'wt_cleanup_helper'
  common_setup
  _wt_cleanup_env
  source "$REPO_ROOT/bin/wt-cleanup"
}

teardown() {
  common_teardown
}

# ── CLI ──────────────────────────────────────────────────────────────────────

@test "wt-cleanup --help exits 0" {
  run main --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"worktrees"* ]]
}

@test "wt-cleanup -h exits 0" {
  run main -h
  [ "$status" -eq 0 ]
}

# ── No worktrees ─────────────────────────────────────────────────────────────

@test "empty worktree list shows 'no stale worktrees'" {
  _write_worktrees <<< '[]'
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
}

# ── Merged worktrees ─────────────────────────────────────────────────────────

@test "merged worktree is removed" {
  _write_worktrees <<'JSON'
[{"branch":"feat/old","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/old"* ]]
  [[ "$output" == *"merged"* ]]
  grep -q "feat/old" "$WT_REMOVE_LOG"
}

@test "merged via symbols field is removed" {
  _write_worktrees <<'JSON'
[{"branch":"feat/done","is_main":false,"is_current":false,"main_state":"diverged","symbols":"⊂ ↑1","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/done"* ]]
}

@test "integrated via main_state field is removed" {
  _write_worktrees <<'JSON'
[{"branch":"feat/merged","is_main":false,"is_current":false,"main_state":"integrated","symbols":"","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/merged"* ]]
}

# ── Squash-merged PRs (GitHub fallback) ──────────────────────────────────────

@test "squash-merged PR detected via gh fallback" {
  _write_worktrees <<'JSON'
[{"branch":"feat/squashed","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑1","commit":{"timestamp":0}}]
JSON
  echo "feat/squashed" > "$GH_PR_MERGED"
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/squashed"* ]]
  [[ "$output" == *"pr merged"* ]]
  grep -q "feat/squashed" "$WT_REMOVE_LOG"
}

@test "unmerged PR not removed by gh fallback" {
  _write_worktrees <<'JSON'
[{"branch":"feat/open-pr","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑3","commit":{"timestamp":0}}]
JSON
  echo "feat/other-branch" > "$GH_PR_MERGED"
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
}

@test "an unreachable tracker leaves the git signals in charge" {
  # A machine with no tracker at all — no auth here, and equally no gh, no
  # network, or a remote that is not GitHub. The sweep runs on git's signals,
  # which is the intended degradation rather than a gap: refusing on these would
  # mean never cleaning up a worktree on such a machine at any point.
  #
  # Distinct from a tracker that declined to answer, which is the case below.
  _write_worktrees <<'JSON'
[{"branch":"feat/unasked","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑1","commit":{"timestamp":0}}]
JSON
  # This case writes no PR fixture. The mock reports `gh auth status` as failing
  # when none of the three exists, and $TMPDIR is per-test, so the tracker is
  # unreachable here without anything having to remove them.
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
  [ ! -f "$WT_REMOVE_LOG" ]
}

@test "a branch git calls integrated is not deleted when the tracker was refused" {
  # The hole this closes. git marks a branch `⊂` the moment the default branch is
  # merged *into* it, which an open PR does routinely — so `⊂` alone is only safe
  # because the open-PR guard ran first. A refused lookup means it never did, and
  # removal here takes the branch with the worktree.
  _write_worktrees <<'JSON'
[{"branch":"feat/integrated","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  echo "feat/integrated" > "$GH_PR_OPEN"
  export GH_THROTTLED=1

  _run_cleanup
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
}

@test "a refused lookup is reported rather than passing for no PR" {
  _write_worktrees <<'JSON'
[{"branch":"feat/integrated","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  export GH_THROTTLED=1

  _run_cleanup
  [ "$status" -eq 0 ]
  # Silence would leave the operator reading "no stale worktrees" as a clean
  # sweep, when it is a sweep that could not ask.
  [[ "$output" == *"would not say whether these branches merged"* ]]
}

@test "a refused lookup warns that --age may still remove worktrees" {
  # "no branch will be deleted this run" reads as "nothing happens" on its own,
  # but an --age removal still takes the worktree while keeping the branch — see
  # "a refused lookup keeps the branch on an age removal" below. The warning has
  # to say so, or an operator relying on it alone is surprised by a vanished
  # worktree.
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/stale","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":$old_timestamp}}]
JSON
  export GH_THROTTLED=1

  _run_cleanup --age 30 --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" == *"no branch will be deleted this run"* ]]
  [[ "$output" == *"--age may still remove their worktrees"* ]]
}

@test "a refused lookup keeps the branch on an age removal" {
  # The worktree still goes — inactivity is a local fact and needs no tracker —
  # but the branch stays. Withholding --force-delete is not enough on its own:
  # `wt remove` deletes the branch on its own ancestry check unless told not to.
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/stale","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":$old_timestamp}}]
JSON
  export GH_THROTTLED=1

  _run_cleanup --age 30 --no-grace-period
  [ "$status" -eq 0 ]
  grep -q "feat/stale" "$WT_REMOVE_LOG"
  grep -q -- "--no-delete-branch" "$WT_REMOVE_LOG"
  ! grep -q -- "--force-delete" "$WT_REMOVE_LOG"
}

@test "a refused lookup keeps a dirty worktree out of the merged summary" {
  # The dirty path reaches _is_merged by its other call site. Naming the worktree
  # "merged, holding changes" invites the operator to discard work on the
  # strength of a read that never happened.
  _write_worktrees <<'JSON'
[{"branch":"feat/dirty","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"modified":true}}]
JSON
  export GH_THROTTLED=1

  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" != *"uncommitted"* ]]
  [ ! -f "$WT_REMOVE_LOG" ]
}

@test "a repo the tracker cannot answer for at all is still cleaned up" {
  # A 404 is permanent for this token and repo, as is a non-GitHub remote. Both
  # must stay best-effort: refusing on them would strand every worktree on a
  # GitLab repo forever, which is the failure mode the refusal must not cause.
  _write_worktrees <<'JSON'
[{"branch":"feat/merged","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  export GH_UNANSWERABLE=1

  _run_cleanup
  [ "$status" -eq 0 ]
  grep -q "feat/merged" "$WT_REMOVE_LOG"
  [[ "$output" != *"would not say whether"* ]]
}

# ── Protected worktrees ──────────────────────────────────────────────────────

@test "main worktree is skipped even if merged" {
  _write_worktrees <<'JSON'
[{"branch":"main","is_main":true,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
}

@test "main branch worktree is skipped even when is_main flag is false" {
  _write_worktrees <<'JSON'
[{"branch":"main","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
}

@test "current worktree is skipped even if merged" {
  _write_worktrees <<'JSON'
[{"branch":"feat/active","is_main":false,"is_current":true,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
}

# ── Age-based removal ────────────────────────────────────────────────────────

@test "old worktree removed with --age flag" {
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/stale","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑3","commit":{"timestamp":$old_timestamp}}]
JSON
  _run_cleanup --age 30
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/stale"* ]]
  [[ "$output" == *"inactive"* ]]
}

@test "recent worktree kept with --age flag" {
  local recent_timestamp
  recent_timestamp=$(( $(date +%s) - 10 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/fresh","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑1","commit":{"timestamp":$recent_timestamp}}]
JSON
  _run_cleanup --age 30
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
}

@test "old unmerged worktree not removed without --age" {
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 200 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/ancient","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑10","commit":{"timestamp":$old_timestamp}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
}

# ── Dry run ──────────────────────────────────────────────────────────────────

@test "--dry-run prints but does not call wt remove" {
  _write_worktrees <<'JSON'
[{"branch":"feat/bye","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"would remove: feat/bye"* ]]
  [ ! -f "$WT_REMOVE_LOG" ]
}

# ── Quiet mode ───────────────────────────────────────────────────────────────

@test "--quiet suppresses output" {
  _write_worktrees <<'JSON'
[{"branch":"feat/silent","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup --quiet
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

# ── Uncommitted changes protection ──────────────────────────────────────────

@test "merged worktree with uncommitted changes is not removed" {
  _write_worktrees <<'JSON'
[{"branch":"feat/dirty","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
}

@test "unmerged worktree with uncommitted changes is silently skipped" {
  _write_worktrees <<'JSON'
[{"branch":"feat/dirty-unmerged","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑3","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
  [[ "$output" != *"uncommitted"* ]]
}

@test "clean worktree is still removed when merged" {
  _write_worktrees <<'JSON'
[{"branch":"feat/clean-merged","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":false,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/clean-merged"* ]]
}

# ── Dirty-merged summary ───────────────────────────────────────────────────

@test "dirty merged worktree shows summary with change types" {
  _write_worktrees <<'JSON'
[{"branch":"feat/dirty","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":true,"renamed":false,"deleted":false}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"Merged worktrees with uncommitted changes"* ]]
  [[ "$output" == *"feat/dirty"* ]]
  [[ "$output" == *"modified"* ]]
  [[ "$output" == *"untracked"* ]]
}

@test "dirty merged summary shows staged changes" {
  _write_worktrees <<'JSON'
[{"branch":"feat/staged","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":true,"modified":false,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat/staged"* ]]
  [[ "$output" == *"staged"* ]]
}

@test "dirty merged summary shows multiple worktrees" {
  _write_worktrees <<'JSON'
[
  {"branch":"feat/a","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":true,"modified":false,"untracked":false,"renamed":false,"deleted":false}},
  {"branch":"feat/b","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":true,"renamed":false,"deleted":false}}
]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat/a"* ]]
  [[ "$output" == *"feat/b"* ]]
}

@test "dirty merged summary suppressed by --quiet" {
  _write_worktrees <<'JSON'
[{"branch":"feat/dirty","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  _run_cleanup --quiet
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "dirty merged summary appears alongside removals" {
  _write_worktrees <<'JSON'
[
  {"branch":"feat/clean","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":false,"untracked":false,"renamed":false,"deleted":false}},
  {"branch":"feat/dirty","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}}
]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/clean"* ]]
  [[ "$output" == *"Merged worktrees with uncommitted changes"* ]]
  [[ "$output" == *"feat/dirty"* ]]
}

@test "squash-merged dirty worktree detected via gh fallback" {
  _write_worktrees <<'JSON'
[{"branch":"feat/squash-dirty","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑1","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  echo "feat/squash-dirty" > "$GH_PR_MERGED"
  _run_cleanup
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
  [[ "$output" == *"Merged worktrees with uncommitted changes"* ]]
  [[ "$output" == *"feat/squash-dirty"* ]]
}
