#!/usr/bin/env bats
# Tests for wt-cleanup — merge detection, age-based removal, dry-run mode,
# quiet mode, and worktree protection (main/current).

setup_file() {
  load 'test_helper'
  MOCK_BIN="$BATS_FILE_TMPDIR/bin"
  mkdir -p "$MOCK_BIN"

  cat > "$MOCK_BIN/wt" <<'FAKEWT'
#!/usr/bin/env bash
if [[ "$1" == "list" ]]; then
  cat "$WT_JSON_FILE"
elif [[ "$1" == "remove" ]]; then
  echo "$*" >> "$WT_REMOVE_LOG_FILE"
fi
FAKEWT
  chmod +x "$MOCK_BIN/wt"

  # `gh pr list` for the whole repo, matching what lib/branch_state.sh calls.
  # The per-state fixture files stay one branch name per line; only the shape
  # of the answer changed.
  cat > "$MOCK_BIN/gh" <<'FAKEGH'
#!/usr/bin/env bash
_emit() {
  local file="$1" state="$2"
  [[ -f "$file" ]] || return 0
  while read -r branch; do
    [[ -n "$branch" ]] || continue
    printf '{"headRefName":"%s","state":"%s"}\n' "$branch" "$state"
  done < "$file"
}

if [[ "$1" == "auth" && "$2" == "status" ]]; then
  [[ -f "$GH_PR_MERGED_FILE" || -f "$GH_PR_OPEN_FILE" || -f "$GH_PR_CLOSED_FILE" ]] && exit 0
  exit 1
elif [[ "$1" == "pr" && "$2" == "list" ]]; then
  { _emit "$GH_PR_MERGED_FILE" MERGED
    _emit "$GH_PR_OPEN_FILE" OPEN
    _emit "$GH_PR_CLOSED_FILE" CLOSED
  } | jq -s '.'
  exit 0
fi
exit 1
FAKEGH
  chmod +x "$MOCK_BIN/gh"

  # Resolved before the mock shadows it on PATH, so the schema contract test
  # can ask the real worktrunk what shape it emits.
  REAL_WT="${REAL_WT-$(command -v wt 2>/dev/null || echo "")}"
  export MOCK_BIN REAL_WT
}

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"

  WT_JSON="$TMPDIR/wt-list.json"
  WT_REMOVE_LOG="$TMPDIR/wt-removes.log"
  GH_PR_MERGED="$TMPDIR/gh-pr-merged.txt"
  GH_PR_OPEN="$TMPDIR/gh-pr-open.txt"
  GH_PR_CLOSED="$TMPDIR/gh-pr-closed.txt"

  export PATH="$MOCK_BIN:$PATH"
  export WT_JSON_FILE="$WT_JSON"
  export WT_REMOVE_LOG_FILE="$WT_REMOVE_LOG"
  export GH_PR_MERGED_FILE="$GH_PR_MERGED"
  export GH_PR_OPEN_FILE="$GH_PR_OPEN"
  export GH_PR_CLOSED_FILE="$GH_PR_CLOSED"
  export CLEANUP_LOG_DIR="$TMPDIR/logs"
  export NO_COLOR=1
  export WORKBENCH_DIR="$REPO_ROOT"

  source "$REPO_ROOT/bin/wt-cleanup"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: run wt-cleanup with mocked wt and gh
_run_cleanup() {
  run main "$@"
}

# Helper: write worktree JSON.
#
# Cases describe a worktree in the concise form this suite has always used
# (`is_main`, `main_state`, `symbols`, `working_tree`, epoch `commit.timestamp`)
# and this translates it into the schema `wt list --format json` actually
# emits. Keeping the mapping here rather than in each case means one edit
# adopts the next schema bump — and `wt list schema matches the fixture shape`
# below fails if this drifts from the real thing.
_write_worktrees() {
  jq --argjson schema "$WT_LIST_SCHEMA" '
    {
      schema: $schema,
      repo: { default_branch: "main" },
      collected: { ci: false, summary: false },
      items: map({
        branch: .branch,
        head: {
          sha: "0000000000000000000000000000000000000000",
          short_sha: "00000000",
          subject: "fixture commit",
          committed_at: (.commit.timestamp // 0 | todate)
        },
        worktree: {
          path: (.path // "/nonexistent/\(.branch)"),
          main: (.is_main // false),
          current: (.is_current // false),
          changes: ((.working_tree // {}) | {
            staged:     (.staged // false),
            modified:   (.modified // false),
            untracked:  (.untracked // false),
            renamed:    (.renamed // false),
            deleted:    (.deleted // false),
            conflicted: (.conflicted // false)
          })
        },
        display: {
          state: (.main_state // ""),
          symbols: (.symbols // "")
        }
      })
    }' > "$WT_JSON"
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

# ── Disposable residue ──────────────────────────────────────────────────────
#
# A merged worktree's residue is judged against the default branch, because the
# branch is already in it: a file the default branch ignores is a file the
# project decided weeks ago was not worth keeping, and a worktree cut before the
# rule landed reports it only because its own copy of the rules is stale. The
# flags `wt list` carries cannot see either — they answer whether the worktree's
# own index calls a file dirty, which for a merged worktree is the wrong
# question.

# _make_worktrees — a repo checked out at `main` with a `feature` worktree of the
# same commit, in MAIN_WT and FEAT_WT. The feature worktree's HEAD is the commit
# main was at when it was cut, which is what lets a test land an ignore rule on
# main that the worktree does not carry.
_make_worktrees() {
  MAIN_WT="$TMPDIR/repo"
  FEAT_WT="$TMPDIR/feature"
  mkdir -p "$MAIN_WT"
  git -C "$MAIN_WT" init -q --initial-branch=main
  git -C "$MAIN_WT" config user.email test@example.com
  git -C "$MAIN_WT" config user.name Test
  printf 'alpha\nbeta\ngamma\n' > "$MAIN_WT/list.txt"
  git -C "$MAIN_WT" add -A
  git -C "$MAIN_WT" commit -qm init
  git -C "$MAIN_WT" worktree add -q -b feature "$FEAT_WT"
}

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

  local real_git fake_bin
  real_git="$(command -v git)"
  fake_bin="$TMPDIR/fake-git-bin"
  mkdir -p "$fake_bin"
  cat > "$fake_bin/git" <<EOF
#!/usr/bin/env bash
if [[ "\$1" == "-C" && "\$2" == "$FEAT_WT" && "\$3" == "status" ]]; then
  exit 1
fi
exec "$real_git" "\$@"
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
  ! grep -q -- "--force-delete" "$WT_REMOVE_LOG"
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

# ── Schema compatibility ─────────────────────────────────────────────────────
#
# The schema this script reads is worktrunk's to change, and it has: the bump
# to 2 moved every field wt-cleanup reads, and because `wt list` was called as
# `... || return 0` the breakage surfaced as a silent no-op on every session
# stop rather than an error. These cover both halves — refusing a schema we do
# not know, and keeping the fixtures honest about the one we do.

@test "an unrecognised wt list schema is refused, not indexed" {
  jq -n '{schema: 99, repo: {}, collected: {}, items: []}' > "$WT_JSON"
  _run_cleanup
  [ "$status" -eq 1 ]
  [[ "$output" == *"schema 99 is not supported"* ]]
}

@test "a payload with no schema field is refused" {
  echo '[]' > "$WT_JSON"
  _run_cleanup
  [ "$status" -eq 1 ]
  [[ "$output" == *"not supported"* ]]
}

@test "a wt list failure is reported rather than exiting clean" {
  # $MOCK_BIN lives in BATS_FILE_TMPDIR and is shared by every test in this
  # file, so the failing stub is put back before returning — otherwise every
  # later test runs against a `wt` that refuses to list.
  local saved="$BATS_TEST_TMPDIR/wt.real-mock"
  cp "$MOCK_BIN/wt" "$saved"
  cat > "$MOCK_BIN/wt" <<'FAKEWT'
#!/usr/bin/env bash
[[ "$1" == "list" ]] && exit 1
exit 0
FAKEWT
  chmod +x "$MOCK_BIN/wt"

  _run_cleanup
  local st="$status" out="$output"

  cp "$saved" "$MOCK_BIN/wt"
  chmod +x "$MOCK_BIN/wt"

  [ "$st" -eq 1 ]
  [[ "$out" == *"wt list --format json failed"* ]]
}

@test "wt list schema matches the fixture shape" {
  # CI installs worktrunk precisely so this runs there — a skip in CI would
  # make the whole check decorative, which is the failure mode this test
  # exists to prevent. Only a developer machine without `wt` may skip, and
  # `skip` has to be the statement itself: called inside an `if` body it sets
  # the skip but does not stop the test, which then runs on an empty payload.
  local real=""
  [[ -n "$REAL_WT" && -x "$REAL_WT" ]] \
    && real=$("$REAL_WT" list --format json 2>/dev/null) || true

  if [[ -z "$real" && -n "${CI:-}" ]]; then
    echo "wt list produced nothing in CI — the schema contract is unchecked" >&2
    return 1
  fi
  if [[ -z "$real" ]]; then
    skip "wt unavailable here"
    return 0
  fi

  # The schema the script pins itself to is the one wt actually speaks.
  [ "$(jq -r '.schema' <<< "$real")" = "$WT_LIST_SCHEMA" ]

  # Every field wt-cleanup reads is present on a real row, so a future bump
  # that moves one fails here instead of no-opping in production.
  [ "$(jq -r '.items | type' <<< "$real")" = "array" ]
  local row
  row=$(jq -r '[.items[] | select(.worktree != null)][0]' <<< "$real")
  [ "$row" != "null" ]
  # `.branch` is null on a detached worktree, which is what `actions/checkout`
  # leaves behind — so this asserts the two shapes the field really has, not
  # the one a developer machine happens to show.
  [[ "$(jq -r '.branch | type' <<< "$row")" =~ ^(string|null)$ ]]
  [ "$(jq -r '.worktree.path | type' <<< "$row")" = "string" ]
  [ "$(jq -r '.worktree.main | type' <<< "$row")" = "boolean" ]
  [ "$(jq -r '.worktree.current | type' <<< "$row")" = "boolean" ]
  [ "$(jq -r '.worktree.changes.staged | type' <<< "$row")" = "boolean" ]
  [ "$(jq -r '.worktree.changes.conflicted | type' <<< "$row")" = "boolean" ]
  [ "$(jq -r '.display.state | type' <<< "$row")" = "string" ]
  [ "$(jq -r '.display.symbols | type' <<< "$row")" = "string" ]
  [ "$(jq -r '.head.committed_at | type' <<< "$row")" = "string" ]

  # The committed_at stamp is the format iso_to_epoch parses.
  local stamp
  stamp=$(jq -r '.head.committed_at' <<< "$row")
  [ -n "$(iso_to_epoch "$stamp")" ]
}

@test "a branch row with no worktree is skipped" {
  jq -n --argjson schema "$WT_LIST_SCHEMA" '
    { schema: $schema, repo: {default_branch: "main"}, collected: {},
      items: [{ branch: "feat/branch-only",
                head: {committed_at: "2026-01-01T00:00:00Z"},
                worktree: null,
                display: {state: "integrated", symbols: "⊂"} }] }' > "$WT_JSON"
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
  [ ! -s "$WT_REMOVE_LOG" ]
}

@test "an unparseable timestamp is not treated as an ancient worktree" {
  jq -n --argjson schema "$WT_LIST_SCHEMA" '
    { schema: $schema, repo: {default_branch: "main"}, collected: {},
      items: [{ branch: "feat/bad-date",
                head: {committed_at: "not-a-date"},
                worktree: {path: "/nonexistent/feat/bad-date", main: false,
                           current: false,
                           changes: {staged: false, modified: false,
                                     untracked: false, renamed: false,
                                     deleted: false, conflicted: false}},
                display: {state: "ahead", symbols: "↑3"} }] }' > "$WT_JSON"
  _run_cleanup --age 30
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
  [ ! -s "$WT_REMOVE_LOG" ]
}

@test "a worktree stopped mid-merge is never removed as clean" {
  # A conflicted file is the least safe residue there is: the worktree holds a
  # half-finished merge and nothing else records it. _change_kind names the
  # code "conflicted", but the ordering loop that builds the returned string
  # once omitted that word, so the whole state was dropped and the worktree
  # read as clean — merged, disposable, force-removed.
  _make_worktrees
  git -C "$FEAT_WT" checkout -q -b conflicting
  printf 'theirs\n' > "$FEAT_WT/list.txt"
  git -C "$FEAT_WT" commit -qam theirs
  printf 'ours\n' > "$MAIN_WT/list.txt"
  git -C "$MAIN_WT" commit -qam ours
  git -C "$FEAT_WT" merge main >/dev/null 2>&1 || true
  # Guard the premise: the fixture must really be mid-merge.
  git -C "$FEAT_WT" status --porcelain | grep -q '^UU' || \
    skip "could not stage a conflict in this git"

  _write_worktrees <<JSON
[
  {"branch":"main","path":"$MAIN_WT","is_main":true,"is_current":false,"main_state":"clean","symbols":"","commit":{"timestamp":0}},
  {"branch":"conflicting","path":"$FEAT_WT","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":false,"untracked":false,"renamed":false,"deleted":false,"conflicted":true}}
]
JSON

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" != *"removing: conflicting"* ]]
  [[ "$output" == *"conflicted"* ]]
  [ ! -s "$WT_REMOVE_LOG" ]
}

@test "a detached worktree is left alone rather than removed as \"null\"" {
  # `actions/checkout` leaves a detached HEAD, so this is the shape CI runs
  # against. Every removal path names the branch to `wt remove`; with no
  # branch there is nothing safe to name, and a detached checkout may be a
  # rebase in progress.
  jq -n --argjson schema "$WT_LIST_SCHEMA" '
    { schema: $schema, repo: {default_branch: "main"}, collected: {},
      items: [{ branch: null,
                head: {committed_at: "2026-01-01T00:00:00Z"},
                worktree: {path: "/nonexistent/detached", main: false,
                           current: false, detached: true,
                           changes: {staged: false, modified: false,
                                     untracked: false, renamed: false,
                                     deleted: false, conflicted: false}},
                display: {state: "integrated", symbols: "⊂"} }] }' > "$WT_JSON"
  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
  [ ! -s "$WT_REMOVE_LOG" ]
}
