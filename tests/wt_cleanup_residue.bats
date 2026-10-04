#!/usr/bin/env bats
# Tests for wt-cleanup — disposable residue, the grace period, the open-PR guard, branch deletion, forensic logging.
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

# ── Disposable residue ──────────────────────────────────────────────────────
#
# A merged worktree's residue is judged against the default branch, because the
# branch is already in it: a file the default branch ignores is a file the
# project decided weeks ago was not worth keeping, and a worktree cut before the
# rule landed reports it only because its own copy of the rules is stale. The
# flags `wt list` carries cannot see either — they answer whether the worktree's
# own index calls a file dirty, which for a merged worktree is the wrong
# question.

# _ignore_on_main PATTERN — commit PATTERN to main's .gitignore, after the
# feature worktree was cut so the worktree does not have it.
_ignore_on_main() {
  printf '%s\n' "$1" >> "$MAIN_WT/.gitignore"
  git -C "$MAIN_WT" add .gitignore
  git -C "$MAIN_WT" commit -qm "ignore $1"
}

# _write_merged_pair FLAGS — the two-worktree list the tests above write by
# hand: main, plus a merged `feature` carrying the working_tree FLAGS.
_write_merged_pair() {
  _write_worktrees <<JSON
[
  {"branch":"main","path":"$MAIN_WT","is_main":true,"is_current":false,"main_state":"clean","symbols":"","commit":{"timestamp":0}},
  {"branch":"feature","path":"$FEAT_WT","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":$1}
]
JSON
}

@test "untracked file the default branch ignores does not hold a worktree back" {
  _make_worktrees
  _ignore_on_main '*.tar.gz'
  printf 'artifact\n' > "$FEAT_WT/build.tar.gz"
  _write_merged_pair '{"staged":false,"modified":false,"untracked":true,"renamed":false,"deleted":false}'

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feature"* ]]
  [[ "$output" != *"uncommitted"* ]]
}

@test "an untracked directory the default branch ignores is forgiven too" {
  # The whole directory is untracked, which git would otherwise collapse into
  # one entry naming the directory — a path the ignore rule under it does not
  # cover, and one the check would refuse for the wrong reason.
  _make_worktrees
  _ignore_on_main 'docs/scratch/'
  mkdir -p "$FEAT_WT/docs/scratch"
  printf 'notes\n' > "$FEAT_WT/docs/scratch/plan.md"
  _write_merged_pair '{"staged":false,"modified":false,"untracked":true,"renamed":false,"deleted":false}'

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feature"* ]]
}

@test "untracked file the default branch does not ignore still holds it back" {
  _make_worktrees
  printf 'real work\n' > "$FEAT_WT/notes.md"
  _write_merged_pair '{"staged":false,"modified":false,"untracked":true,"renamed":false,"deleted":false}'

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
  [[ "$output" == *"Merged worktrees with uncommitted changes"* ]]
  [[ "$output" == *"untracked"* ]]
}

@test "a file whose lines only moved does not hold a worktree back" {
  _make_worktrees
  printf 'gamma\nalpha\nbeta\n' > "$FEAT_WT/list.txt"
  _write_merged_pair '{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}'

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feature"* ]]
}

@test "a file that gained a line still holds a worktree back" {
  _make_worktrees
  printf 'gamma\nalpha\nbeta\ndelta\n' > "$FEAT_WT/list.txt"
  _write_merged_pair '{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}'

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
  [[ "$output" == *"modified"* ]]
}

@test "the summary names only the changes that survived the check" {
  # The flags would have said "modified, untracked" for this worktree. Reporting
  # a kind that was forgiven is what makes the warning stop being read.
  _make_worktrees
  _ignore_on_main '*.tar.gz'
  printf 'artifact\n' > "$FEAT_WT/build.tar.gz"
  printf 'alpha\nbeta\ngamma\ndelta\n' > "$FEAT_WT/list.txt"
  _write_merged_pair '{"staged":false,"modified":true,"untracked":true,"renamed":false,"deleted":false}'

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" == *"modified"* ]]
  [[ "$output" != *"untracked"* ]]
}

@test "a deleted file is never forgiven" {
  _make_worktrees
  rm "$FEAT_WT/list.txt"
  _write_merged_pair '{"staged":false,"modified":false,"untracked":false,"renamed":false,"deleted":true}'

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
  [[ "$output" == *"deleted"* ]]
}

@test "every path forgiven is named in the cleanup log" {
  _make_worktrees
  _ignore_on_main '*.tar.gz'
  printf 'artifact\n' > "$FEAT_WT/build.tar.gz"
  _write_merged_pair '{"staged":false,"modified":false,"untracked":true,"renamed":false,"deleted":false}'

  _run_cleanup --quiet --no-grace-period
  [ "$status" -eq 0 ]
  local log_file="$TMPDIR/logs/wt-cleanup.log"
  grep -q "DISPOSABLE-PATH branch=feature path=build.tar.gz reason=ignored-on-" "$log_file"
  grep -q "DISPOSABLE-RESIDUE branch=feature" "$log_file"
}

@test "a reordered file is named in the cleanup log with its reason" {
  _make_worktrees
  printf 'gamma\nalpha\nbeta\n' > "$FEAT_WT/list.txt"
  _write_merged_pair '{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}'

  _run_cleanup --quiet --no-grace-period
  [ "$status" -eq 0 ]
  grep -q "DISPOSABLE-PATH branch=feature path=list.txt reason=reordered" \
    "$TMPDIR/logs/wt-cleanup.log"
}

@test "an unreadable work-tree path leaves the flags in charge" {
  # Every other test in this file names no path at all, which is this case: the
  # check cannot open the work tree, so nothing is forgiven and the worktree is
  # reported exactly as it was before.
  _write_worktrees <<JSON
[{"branch":"feat/gone","path":"$TMPDIR/no-such-worktree","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
  [[ "$output" == *"feat/gone"* ]]
  [[ "$output" == *"modified"* ]]
}

@test "a git status failure leaves a merged worktree's residue in the flags' charge" {
  # A transient failure (e.g. index.lock held by another session) must not read
  # as "nothing here is worth keeping" — it must fall back to the flags `wt
  # list` already carries, the same as an unreadable path.
  _make_worktrees
  printf 'real work\n' > "$FEAT_WT/notes.md"
  _write_merged_pair '{"staged":false,"modified":false,"untracked":true,"renamed":false,"deleted":false}'

  local fake_bin
  fake_bin="$TMPDIR/fake-git-bin"
  mkdir -p "$fake_bin"
  cat > "$fake_bin/git" <<EOF
#!/usr/bin/env bash
if [[ "\$1" == "-C" && "\$2" == "$FEAT_WT" && "\$3" == "status" ]]; then
  exit 1
fi
$(shim_untrap "$fake_bin")
exec git "\$@"
EOF
  chmod +x "$fake_bin/git"

  local old_path="$PATH"
  export PATH="$fake_bin:$PATH"
  _run_cleanup --no-grace-period
  export PATH="$old_path"

  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
  [[ "$output" == *"Merged worktrees with uncommitted changes"* ]]
  [[ "$output" == *"untracked"* ]]
}

@test "an unmerged worktree with only disposable residue is removed by age" {
  # Disposable residue is judged the same way regardless of merge state: an
  # ignored file is not work to lose whether or not the branch has landed.
  _make_worktrees
  _ignore_on_main '*.tar.gz'
  printf 'artifact\n' > "$FEAT_WT/build.tar.gz"
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))
  _write_worktrees <<JSON
[
  {"branch":"main","path":"$MAIN_WT","is_main":true,"is_current":false,"main_state":"clean","symbols":"","commit":{"timestamp":0}},
  {"branch":"feature","path":"$FEAT_WT","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑1","commit":{"timestamp":$old_timestamp},"working_tree":{"staged":false,"modified":false,"untracked":true,"renamed":false,"deleted":false}}
]
JSON

  _run_cleanup --age 30 --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feature"* ]]
  [[ "$output" == *"inactive"* ]]
}

# ── Grace period ────────────────────────────────────────────────────────────

@test "recently created worktree is skipped by grace period" {
  # Create a real directory so stat works
  local wt_dir="$TMPDIR/recent-worktree"
  mkdir -p "$wt_dir"
  _write_worktrees <<JSON
[{"branch":"feat/new","path":"$wt_dir","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipping: feat/new"* ]]
  [[ "$output" == *"grace period"* ]]
}

@test "--no-grace-period removes recently created worktree" {
  local wt_dir="$TMPDIR/recent-worktree"
  mkdir -p "$wt_dir"
  _write_worktrees <<JSON
[{"branch":"feat/new","path":"$wt_dir","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/new"* ]]
}

# ── Open PR guard ──────────────────────────────────────────────────────────

@test "worktree with open PR is not removed even if integrated" {
  _write_worktrees <<'JSON'
[{"branch":"feat/open","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  echo "feat/open" > "$GH_PR_OPEN"
  _run_cleanup
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
}

@test "worktree with open PR is not removed by squash-merge fallback" {
  _write_worktrees <<'JSON'
[{"branch":"feat/pr-open","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑2","commit":{"timestamp":0}}]
JSON
  echo "feat/pr-open" > "$GH_PR_OPEN"
  _run_cleanup
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
}

@test "open PR shown in dry-run skip" {
  _write_worktrees <<'JSON'
[{"branch":"feat/guarded","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  echo "feat/guarded" > "$GH_PR_OPEN"
  _run_cleanup --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipping: feat/guarded"* ]]
  [[ "$output" == *"open PR"* ]]
}

@test "old worktree with open PR is not removed by age" {
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/old-pr","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑3","commit":{"timestamp":$old_timestamp}}]
JSON
  echo "feat/old-pr" > "$GH_PR_OPEN"
  _run_cleanup --age 30
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
}

@test "closed PR is not guarded — integrated worktree still removed" {
  _write_worktrees <<'JSON'
[{"branch":"feat/closed","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  echo "feat/closed" > "$GH_PR_CLOSED"
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/closed"* ]]
  grep -q "feat/closed" "$WT_REMOVE_LOG"
}

@test "dirty worktree with open PR is not added to dirty-merged summary" {
  _write_worktrees <<'JSON'
[{"branch":"feat/dirty-open","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  echo "feat/dirty-open" > "$GH_PR_OPEN"
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" != *"feat/dirty-open"* ]]
}

# ── Branch deletion ────────────────────────────────────────────────────────
#
# `wt` decides on its own whether the branch goes with the worktree, and asks an
# ancestry check a squash merge defeats. Where this script has already proved
# the branch merged it says so with --force-delete; where it has only proved the
# worktree idle it says nothing and the branch survives.

@test "a merged removal deletes the branch with the worktree" {
  _write_worktrees <<'JSON'
[{"branch":"feat/gone","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  grep -q -- "--force-delete" "$WT_REMOVE_LOG"
}

# `wt remove` without --foreground hands the delete to `taskpolicy -b`, and on
# macOS entering background QoS can block in setpriority for as long as other
# I/O keeps the disk busy — the cleanup then hangs behind a test suite.
@test "every removal runs in the foreground" {
  _write_worktrees <<'JSON'
[{"branch":"feat/gone","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  grep -q -- "--foreground" "$WT_REMOVE_LOG"
}

@test "a squash-merged removal deletes the branch too" {
  _write_worktrees <<'JSON'
[{"branch":"feat/squash-gone","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑1","commit":{"timestamp":0}}]
JSON
  echo "feat/squash-gone" > "$GH_PR_MERGED"
  _run_cleanup
  [ "$status" -eq 0 ]
  grep -q -- "--force-delete" "$WT_REMOVE_LOG"
}

@test "an age removal keeps the branch" {
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/idle","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑3","commit":{"timestamp":$old_timestamp}}]
JSON
  _run_cleanup --age 30
  [ "$status" -eq 0 ]
  grep -q "feat/idle" "$WT_REMOVE_LOG"
  run ! grep -q -- "--force-delete" "$WT_REMOVE_LOG"
}

@test "a branch merged and idle at once is still deleted" {
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/old-and-merged","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":$old_timestamp}}]
JSON
  _run_cleanup --age 30
  [ "$status" -eq 0 ]
  grep -q -- "--force-delete" "$WT_REMOVE_LOG"
}

@test "a dry run records the deletion it would have made" {
  _write_worktrees <<'JSON'
[{"branch":"feat/would-go","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup --dry-run
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
  local log_file="$TMPDIR/logs/wt-cleanup.log"
  grep -q "DRY-REMOVE branch=feat/would-go" "$log_file"
  grep -q "delete_branch=true" "$log_file"
}

@test "a dry run of an age removal records the branch it would keep" {
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/would-stay","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑3","commit":{"timestamp":$old_timestamp}}]
JSON
  _run_cleanup --dry-run --age 30
  [ "$status" -eq 0 ]
  local log_file="$TMPDIR/logs/wt-cleanup.log"
  grep -q "DRY-REMOVE branch=feat/would-stay" "$log_file"
  grep -q "delete_branch=false" "$log_file"
}

@test "the worktree force flag is still passed alongside" {
  _write_worktrees <<'JSON'
[{"branch":"feat/both","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  grep -q -- "--force " "$WT_REMOVE_LOG"
  grep -q -- "--force-delete" "$WT_REMOVE_LOG"
}

# ── Forensic logging ──────────────────────────────────────────────────────

@test "removal is logged even in quiet mode" {
  _write_worktrees <<'JSON'
[{"branch":"feat/logged","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup --quiet
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  local log_file="$TMPDIR/logs/wt-cleanup.log"
  [ -f "$log_file" ]
  grep -q "REMOVE branch=feat/logged" "$log_file"
  grep -q "reason=merged" "$log_file"
  grep -q "delete_branch=true" "$log_file"
}

@test "an age removal logs that the branch was kept" {
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/logged-idle","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑3","commit":{"timestamp":$old_timestamp}}]
JSON
  _run_cleanup --quiet --age 30
  [ "$status" -eq 0 ]
  local log_file="$TMPDIR/logs/wt-cleanup.log"
  grep -q "REMOVE branch=feat/logged-idle" "$log_file"
  grep -q "delete_branch=false" "$log_file"
}

@test "open-PR skip is logged" {
  _write_worktrees <<'JSON'
[{"branch":"feat/logged-open","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑1","commit":{"timestamp":0}}]
JSON
  echo "feat/logged-open" > "$GH_PR_OPEN"
  _run_cleanup --quiet
  [ "$status" -eq 0 ]
  local log_file="$TMPDIR/logs/wt-cleanup.log"
  [ -f "$log_file" ]
  grep -q "SKIP-OPEN-PR branch=feat/logged-open" "$log_file"
}
