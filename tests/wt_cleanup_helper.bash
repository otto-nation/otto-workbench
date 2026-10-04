#!/usr/bin/env bash
# Mocks and fixtures shared by the wt_cleanup_* suites.

# _wt_cleanup_mocks — write the mock wt and gh under $BATS_FILE_TMPDIR/bin and
# export MOCK_BIN and REAL_WT. Called from each suite's setup_file.
_wt_cleanup_mocks() {
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

  # The per-branch REST read lib/branch_state.sh makes. The per-state fixture
  # files stay one branch name per line; the mock answers for the single branch
  # named in the endpoint's `head=` filter, which is what lets a case assert that
  # a branch was never asked about.
  #
  # It returns the verdict directly rather than a PR array put through the
  # library's --jq. The reduction from (state, merged_at) is branch_state.bats'
  # subject; here the fixtures are already states, and reconstructing REST rows
  # just to reduce them back would be a second copy of that logic in the mock.
  #
  # The branches asked about are recorded so `only branches with a worktree are
  # asked about` can read them; every other case ignores the log.
  cat > "$MOCK_BIN/gh" <<'FAKEGH'
#!/usr/bin/env bash
_state_of() {
  local branch="$1"
  # Precedence matches the library's: OPEN, then MERGED, then CLOSED.
  grep -qxF "$branch" "$GH_PR_OPEN_FILE"   2>/dev/null && { printf 'OPEN';   return; }
  grep -qxF "$branch" "$GH_PR_MERGED_FILE" 2>/dev/null && { printf 'MERGED'; return; }
  grep -qxF "$branch" "$GH_PR_CLOSED_FILE" 2>/dev/null && { printf 'CLOSED'; return; }
}

if [[ "$1" == "auth" && "$2" == "status" ]]; then
  # A spent quota and a repo gh cannot resolve both leave auth working — the
  # refusal comes from the request, not from the login.
  [[ -n "${GH_THROTTLED:-}${GH_UNANSWERABLE:-}" ]] && exit 0
  [[ -f "$GH_PR_MERGED_FILE" || -f "$GH_PR_OPEN_FILE" || -f "$GH_PR_CLOSED_FILE" ]] && exit 0
  exit 1
elif [[ "$1" == "api" ]]; then
  # GitHub declining to answer a knowable question. Its wording is what the
  # library classifies on, and it goes to stderr as the real one does.
  if [[ -n "${GH_THROTTLED:-}" ]]; then
    echo "gh: API rate limit exceeded for user ID 1234. (HTTP 403)" >&2
    exit 1
  fi
  # A question this repo could never answer — no GitHub remote, or a repo the
  # token cannot see. Permanent, and must stay best-effort.
  if [[ -n "${GH_UNANSWERABLE:-}" ]]; then
    echo "gh: Not Found (HTTP 404)" >&2
    exit 1
  fi
  endpoint="$2"
  filter=""
  [[ "$endpoint" == *"head="* ]] && filter="${endpoint#*head=}" && filter="${filter%%&*}"

  # `{owner}:branch`, url-encoded. A filter the library built without the owner
  # prefix names no branch, and is answered as such rather than silently.
  [[ "$filter" == *:* ]] || exit 1
  branch="$(printf '%b' "${filter#*:}" | sed 's/%2F/\//g; s/%2f/\//g')"
  [[ -n "$branch" ]] || exit 1

  printf '%s\n' "$branch" >> "$GH_BRANCH_LOG"
  _state_of "$branch"
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

# _wt_cleanup_env — point the mocks at this test's fixture files and put them
# first on PATH. Called from each suite's setup, before it sources wt-cleanup.
_wt_cleanup_env() {
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
  export GH_BRANCH_LOG="$TMPDIR/gh-branches.log"
  : > "$GH_BRANCH_LOG"
  unset GH_THROTTLED GH_UNANSWERABLE
  export CLEANUP_LOG_DIR="$TMPDIR/logs"
  export NO_COLOR=1
  export WORKBENCH_DIR="$REPO_ROOT"
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
  # A commit of its own, because every caller describes this worktree as
  # merged and a branch with no commits is unstarted rather than merged —
  # `_is_unstarted` in bin/wt-cleanup keeps those, so a fixture without one
  # would be testing that guard instead of the residue checks these cases are
  # about.
  printf 'work\n' > "$FEAT_WT/worked.txt"
  git -C "$FEAT_WT" add -A
  git -C "$FEAT_WT" commit -qm "work on feature"
}

# _make_unstarted_worktree — a feature worktree whose branch holds no commit,
# in a repo that ignores `ignore/` from its first commit.
#
# Separate from `_make_worktrees` because the ignore rule has to predate the
# worktree: `_ignore_on_main` commits it afterwards, so the worktree never
# carries it and git reports the file as untracked rather than ignored. That
# is a different case, covered in wt_cleanup_residue.bats.
_make_unstarted_worktree() {
  MAIN_WT="$TMPDIR/repo"
  FEAT_WT="$TMPDIR/feature"
  mkdir -p "$MAIN_WT"
  git -C "$MAIN_WT" init -q --initial-branch=main
  git -C "$MAIN_WT" config user.email test@example.com
  git -C "$MAIN_WT" config user.name Test
  printf 'ignore/\n' > "$MAIN_WT/.gitignore"
  printf 'alpha\n' > "$MAIN_WT/list.txt"
  git -C "$MAIN_WT" add -A
  git -C "$MAIN_WT" commit -qm init
  git -C "$MAIN_WT" worktree add -q -b feature "$FEAT_WT"
}
