#!/usr/bin/env bats
# Tests for the Claude guard hooks: hook behaviour, the edit-guard tree lock, PATH binaries, statement anchoring, multi-line and sh -c scanning.
setup_file() {
  load 'test_helper'
  load 'claude_settings_helper'
  local repo_root
  repo_root="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

  _sync_settings_into "$BATS_FILE_TMPDIR/home" "$repo_root"
}

setup() {
  load 'test_helper'
  load 'claude_settings_helper'
  common_setup
  SETTINGS="$REPO_ROOT/ai/claude/settings.json"
}

teardown() {
  common_teardown
}

# ── Hook behavior ────────────────────────────────────────────────────────────

@test "settings delegates every Bash rule to the guard script" {
  local bin_dir cmds
  bin_dir=$(sed -n 's/^LOCAL_BIN_DIR="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
  [ -n "$bin_dir" ]

  cmds=$(jq -r '.hooks.PreToolUse[] | select(.matcher == "Bash") | .hooks[].command' "$SETTINGS")
  [ "$cmds" = "bash $bin_dir/claude-bash-guard" ] || {
    echo "expected a single guard invocation, got:"
    echo "$cmds"
    return 1
  }
}

@test "settings delegates Edit|Write to claude-edit-guard" {
  local bin_dir cmds
  bin_dir=$(sed -n 's/^LOCAL_BIN_DIR="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
  [ -n "$bin_dir" ]

  cmds=$(jq -r '.hooks.PreToolUse[] | select(.matcher == "Edit|Write") | .hooks[].command' "$SETTINGS")
  # Two hooks share this matcher: the edit guard that can block, and the
  # session-lock refresh that re-records a claim which went missing. Asserted
  # as an exact list rather than a substring, so a third arrival is a decision
  # somebody makes here rather than one that lands silently.
  local expected
  expected="bash $bin_dir/claude-edit-guard
bash $bin_dir/claude-session-lock --refresh"
  [ "$cmds" = "$expected" ] || {
    echo "expected the edit guard then the session-lock refresh, got:"
    echo "$cmds"
    return 1
  }
}

@test "settings records and releases the session lock" {
  # An unattended fix pass refuses on this record, so a session that never
  # writes one is a session a pass will commit over. SessionEnd and not Stop:
  # Stop fires after every assistant turn, so releasing there would drop the
  # claim between turns while the session edits on.
  local bin_dir start end
  bin_dir=$(sed -n 's/^LOCAL_BIN_DIR="\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
  [ -n "$bin_dir" ]

  start=$(jq -r '.hooks.SessionStart[].hooks[].command' "$SETTINGS")
  [[ "$start" == *"claude-session-lock --acquire"* ]] || {
    echo "SessionStart does not acquire the session lock:"; echo "$start"; return 1
  }

  end=$(jq -r '.hooks.SessionEnd[].hooks[].command' "$SETTINGS")
  [ "$end" = "bash $bin_dir/claude-session-lock --release" ] || {
    echo "SessionEnd does not release the session lock:"; echo "$end"; return 1
  }

  [ "$(jq -r '.hooks.Stop[].hooks[].command' "$SETTINGS" | grep -c session-lock)" -eq 0 ]
}

@test "guard: exits 0 on a payload with no command" {
  run _run_guard '{"tool_input":{}}'
  [ "$status" -eq 0 ]
}

@test "guard: fails open on a malformed payload" {
  run _run_guard 'not json'
  [ "$status" -eq 0 ]
}

@test "brace hook: blocks real brace expansion" {
  run _run_guard '{"tool_input":{"command":"cp file.{txt,bak}"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Brace expansion"* ]]
}

@test "brace hook: allows heredoc with braces in body" {
  local cmd
  cmd=$(printf 'python3 << '\''PYEOF'\''\nd = {"a": 1, "b": 2}\nPYEOF')
  run _run_guard "{\"tool_input\":{\"command\":$(jq -Rsa '.' <<< "$cmd")}}"
  [ "$status" -eq 0 ]
}

@test "brace hook: allows python -c with dict in double quotes" {
  run _run_guard '{"tool_input":{"command":"python3 -c \"d = {\\\"a\\\": 1, \\\"b\\\": 2}\""}}'
  [ "$status" -eq 0 ]
}

@test "brace hook: allows jq with braces in single quotes" {
  run _run_guard "{\"tool_input\":{\"command\":\"jq '.items[] | {name, value}' file.json\"}}"
  [ "$status" -eq 0 ]
}

# Runs the Edit/Write PreToolUse guard against a mock payload.
_run_edit_guard() {
  echo "$1" | "$REPO_ROOT/ai/claude/bin/claude-edit-guard" 2>&1
}

_init_test_repo() {
  local dir=$1 branch=${2:-main}
  git -C "$dir" init -b "$branch" --quiet
  git -C "$dir" config user.email "test@example.com"
  git -C "$dir" config user.name "Test"
}

@test "edit-guard: exits 0 on a payload with no file_path" {
  run _run_edit_guard '{"tool_input":{}}'
  [ "$status" -eq 0 ]
}

@test "edit-guard: fails open on a malformed payload" {
  run _run_edit_guard 'not json'
  [ "$status" -eq 0 ]
}

@test "edit-guard: blocks a tracked file on main and names wt switch" {
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  _init_test_repo "$repo"
  touch "$repo/tracked.txt"
  git -C "$repo" add tracked.txt
  git -C "$repo" commit -m "init" --quiet
  run _run_edit_guard "{\"tool_input\":{\"file_path\":\"$repo/tracked.txt\"}}"
  [ "$status" -eq 2 ]
  [ "$output" = "BLOCKED: Cannot edit files on main. Create a worktree first: wt switch -c <branch>" ]
}

@test "edit-guard: blocks a tracked file on master" {
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  _init_test_repo "$repo" master
  touch "$repo/tracked.txt"
  git -C "$repo" add tracked.txt
  git -C "$repo" commit -m "init" --quiet
  run _run_edit_guard "{\"tool_input\":{\"file_path\":\"$repo/tracked.txt\"}}"
  [ "$status" -eq 2 ]
  [ "$output" = "BLOCKED: Cannot edit files on master. Create a worktree first: wt switch -c <branch>" ]
}

@test "edit-guard: allows a gitignored file on main" {
  # Not `ignore/specs/` — that is a plan path, which the workspace rule
  # refuses wherever it lands. This case is about the gitignore exemption to
  # the branch rule, so it needs a path only that rule has an opinion about.
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  _init_test_repo "$repo"
  echo "ignore/" > "$repo/.gitignore"
  git -C "$repo" add .gitignore
  git -C "$repo" commit -m "init" --quiet
  mkdir -p "$repo/ignore/build"
  run _run_edit_guard "{\"tool_input\":{\"file_path\":\"$repo/ignore/build/test.md\"}}"
  [ "$status" -eq 0 ]
}

@test "edit-guard: a gitignored plan path is still refused" {
  # The precedence between the two rules, asserted rather than left to
  # whichever check happens to run first. Being gitignored is what a plan in a
  # worktree already is, and it is no help: `wt remove` deletes ignored files
  # with the tree, so the exemption that makes sense for build output is
  # exactly wrong here.
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  _init_test_repo "$repo"
  echo "ignore/" > "$repo/.gitignore"
  git -C "$repo" add .gitignore
  git -C "$repo" commit -m "init" --quiet

  run _run_edit_guard "{\"tool_input\":{\"file_path\":\"$repo/ignore/specs/test.md\"}}"

  [ "$status" -eq 2 ]
  # The fixture is an ordinary clone, so the refusal is the one that says there
  # is nowhere correct to put it rather than the one naming a workspace. Pinned
  # on `wt-init` because a refusal that does not say how to proceed is the
  # failure mode of a guard nobody can satisfy.
  [[ "$output" == *"ordinary clone"* ]]
  [[ "$output" == *"wt-init"* ]]
}

@test "edit-guard: allows any file on a feature branch" {
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  _init_test_repo "$repo"
  touch "$repo/file.txt"
  git -C "$repo" add file.txt
  git -C "$repo" commit -m "init" --quiet
  git -C "$repo" checkout -b feature --quiet
  run _run_edit_guard "{\"tool_input\":{\"file_path\":\"$repo/file.txt\"}}"
  [ "$status" -eq 0 ]
}

# ── edit-guard: the tree-validation lock ────────────────────────────────────
# The Pi half of this is tree-lock-guard in tests/pi_extensions.bats. Both read
# ai/lib/core/tree_lock.py through `with-tree-lock --check`, so the two
# harnesses cannot disagree about whether a tree is under validation.

# _hold_tree lives in tests/test_helper.bash: pi_extensions.bats holds the same
# lock for the Pi half of this guard, and one fact read by two harnesses is
# worth one helper rather than two copies of it.

@test "edit-guard: blocks an edit to a tree a validator holds" {
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  _init_test_repo "$repo" feature
  touch "$repo/file.txt"
  git -C "$repo" add file.txt
  git -C "$repo" commit -m "init" --quiet
  local holder
  holder="$(_hold_tree "$repo")"
  run _run_edit_guard "{\"tool_input\":{\"file_path\":\"$repo/file.txt\"}}"
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  [ "$status" -eq 2 ]
  [[ "$output" == *"A validator holds this tree"* ]]
  [[ "$output" != *[Pp]id* ]]
}

@test "edit-guard: a gitignored path is still refused while the tree is held" {
  # The gitignore exemption belongs to the main-branch rule, not to this one:
  # a suite reads ignored build output too, and rewriting it underneath a
  # running gate invalidates the same run.
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  _init_test_repo "$repo" feature
  echo "ignore/" > "$repo/.gitignore"
  git -C "$repo" add .gitignore
  git -C "$repo" commit -m "init" --quiet
  mkdir -p "$repo/ignore"
  local holder
  holder="$(_hold_tree "$repo")"
  run _run_edit_guard "{\"tool_input\":{\"file_path\":\"$repo/ignore/out.md\"}}"
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  [ "$status" -eq 2 ]
  [[ "$output" == *"A validator holds this tree"* ]]
}

@test "edit-guard: WORKBENCH_TREE_LOCK does not suppress the refusal" {
  # That variable is the writers' reentrancy marker. A reader honouring it
  # would let anything that inherited it edit straight through the lock.
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  _init_test_repo "$repo" feature
  touch "$repo/file.txt"
  git -C "$repo" add file.txt
  git -C "$repo" commit -m "init" --quiet
  local holder
  holder="$(_hold_tree "$repo")"
  run env WORKBENCH_TREE_LOCK="$repo" bash -c \
    "echo '{\"tool_input\":{\"file_path\":\"$repo/file.txt\"}}' | '$REPO_ROOT/ai/claude/bin/claude-edit-guard' 2>&1"
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  [ "$status" -eq 2 ]
  [[ "$output" == *"A validator holds this tree"* ]]
}

@test "edit-guard: a free tree is not refused" {
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  _init_test_repo "$repo" feature
  touch "$repo/file.txt"
  git -C "$repo" add file.txt
  git -C "$repo" commit -m "init" --quiet
  run _run_edit_guard "{\"tool_input\":{\"file_path\":\"$repo/file.txt\"}}"
  [ "$status" -eq 0 ]
}

@test "edit-guard: inherited GIT_DIR does not retarget the probe" {
  # Git skips discovery when GIT_DIR is set, so an inherited one would make
  # `rev-parse --show-toplevel` answer about the calling hook's repository and
  # leave the guard probing the lock on a tree nobody is editing.
  local repo="$TMPDIR/repo" other="$TMPDIR/other"
  mkdir -p "$repo" "$other"
  _init_test_repo "$repo" feature
  touch "$repo/file.txt"
  git -C "$repo" add file.txt
  git -C "$repo" commit -m "init" --quiet
  _init_test_repo "$other" feature
  git -C "$other" commit --allow-empty -m init --quiet
  local holder other_git
  holder="$(_hold_tree "$repo")"
  other_git=$(git -C "$other" rev-parse --absolute-git-dir)
  run env GIT_DIR="$other_git" GIT_WORK_TREE="$other" bash -c \
    "echo '{\"tool_input\":{\"file_path\":\"$repo/file.txt\"}}' | '$REPO_ROOT/ai/claude/bin/claude-edit-guard' 2>&1"
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  [ "$status" -eq 2 ]
  [[ "$output" == *"A validator holds this tree"* ]]
}

@test "edit-guard: the lock is read when the guard runs through its install symlink" {
  # settings.json runs the guard as ~/.local/bin/claude-edit-guard, the
  # symlink sync_component_bin installs. BASH_SOURCE is that path, so without
  # following it the ../../.. traversal lands outside the repo, the -x test
  # fails, and the lock half is skipped everywhere but here.
  local repo="$TMPDIR/repo" bindir="$TMPDIR/bin"
  mkdir -p "$repo" "$bindir"
  _init_test_repo "$repo" feature
  touch "$repo/file.txt"
  git -C "$repo" add file.txt
  git -C "$repo" commit -m "init" --quiet
  ln -s "$REPO_ROOT/ai/claude/bin/claude-edit-guard" "$bindir/claude-edit-guard"
  local holder
  holder="$(_hold_tree "$repo")"
  run bash -c \
    "echo '{\"tool_input\":{\"file_path\":\"$repo/file.txt\"}}' | bash '$bindir/claude-edit-guard' 2>&1"
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  [ "$status" -eq 2 ]
  [[ "$output" == *"A validator holds this tree"* ]]
}

# ── gh pr create block ──────────────────────────────────────────────────────

@test "pr create hook: blocks gh pr create" {
  run _run_guard '{"tool_input":{"command":"gh pr create"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"BLOCKED"* ]]
}

@test "pr create hook: blocks gh pr create --draft" {
  run _run_guard '{"tool_input":{"command":"gh pr create --draft --title \"fix: thing\""}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"BLOCKED"* ]]
}

@test "pr create hook: allows gh pr list" {
  run _run_guard '{"tool_input":{"command":"gh pr list --state open"}}'
  [ "$status" -eq 0 ]
}

@test "pr create hook: allows gh pr view" {
  run _run_guard '{"tool_input":{"command":"gh pr view 42 --json state"}}'
  [ "$status" -eq 0 ]
}

@test "pr create hook: allows gh api" {
  run _run_guard '{"tool_input":{"command":"gh api repos/owner/repo/pulls"}}'
  [ "$status" -eq 0 ]
}

# ── PATH binary absolute paths ──────────────────────────────────────────────
# The allow list keys on the bare command name, so `Bash(cat:*)` never matches
# `/bin/cat` and `Bash(mise:*)` never matches `~/.local/bin/mise` — the absolute
# form prompts on every call. The rule covers every bin/ on the default PATH,
# plus a version manager's shim and install dirs. It rides the same quote-
# stripped text as the guardrails below, so such a path inside a quoted
# argument is not mistaken for an invocation.

@test "reposcript hook: blocks an absolute path to a bin/local script" {
  run _run_guard '{"tool_input":{"command":"/Users/me/git/repo/bin/local/validate-all"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"bin/local/validate-all"* ]]
}

@test "reposcript hook: blocks an absolute path after a statement separator" {
  run _run_guard '{"tool_input":{"command":"ls -la; /Users/me/git/repo/bin/local/validate-all"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"bin/local/validate-all"* ]]
}

@test "reposcript hook: names the git/ prefixed path" {
  run _run_guard '{"tool_input":{"command":"/Users/me/git/repo/git/bin/local/generate-git-rules"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"'git/bin/local/generate-git-rules'"* ]]
}

# ai/bin sits under a `bin/` of its own, so the longest granted directory has to
# win — naming it `bin/review-post` would suggest a path that does not exist.
@test "reposcript hook: names the ai/bin path in full" {
  run _run_guard '{"tool_input":{"command":"/Users/me/git/repo/ai/bin/review-post --pr 1"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"'ai/bin/review-post'"* ]]
}

@test "reposcript hook: blocks a ./ prefix on a top-level bin script" {
  run _run_guard '{"tool_input":{"command":"./bin/otto-workbench sync"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"'bin/otto-workbench'"* ]]
}

@test "reposcript hook: blocks a ./ prefix on an ai/bin script" {
  run _run_guard '{"tool_input":{"command":"./ai/bin/otto-log stats"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"'ai/bin/otto-log'"* ]]
}

# The repo ships bin/ directories that no rule grants. A `./` prefix names the
# repo root, so the granted directory has to sit at the start of what follows —
# claiming the `bin/` anywhere in the path would send Claude at a top-level
# script of that name, and there is none.
@test "reposcript hook: leaves a ./ prefixed nested bin directory alone" {
  local script
  for script in ai/serena/bin/serena-mcp docker/bin/cleanup-testcontainers; do
    [ -f "$REPO_ROOT/$script" ] || { echo "no such script: $script"; return 1; }
    run _run_guard "{\"tool_input\":{\"command\":\"./$script\"}}"
    [ "$status" -eq 0 ] || { echo "blocked ./$script: $output"; return 1; }
  done
}

@test "reposcript hook: leaves the relative form of a nested bin directory alone" {
  run _run_guard '{"tool_input":{"command":"ai/serena/bin/serena-mcp"}}'
  [ "$status" -eq 0 ]
}

# ai/bin is granted, so its repo-root-relative form is the one the allow list
# matches — the guard has nothing to steer.
@test "reposcript hook: leaves the relative form of an ai/bin script alone" {
  run _run_guard '{"tool_input":{"command":"ai/bin/otto-log stats"}}'
  [ "$status" -eq 0 ]
}

# The root anchor holds for every granted directory, not only the bare `bin` the
# repo's own nested bin/ directories collide with — a vendored checkout carrying
# its own ai/claude/bin or git/bin is not the granted one either.
@test "reposcript hook: leaves a ./ prefixed nested multi-component directory alone" {
  local dir
  for dir in ai/claude/bin git/bin bin/local; do
    run _run_guard "{\"tool_input\":{\"command\":\"./vendor/$dir/otto-log\"}}"
    [ "$status" -eq 0 ] || { echo "blocked ./vendor/$dir/otto-log: $output"; return 1; }
  done
}

@test "reposcript hook: allows the relative form" {
  run _run_guard '{"tool_input":{"command":"bin/local/validate-all"}}'
  [ "$status" -eq 0 ]
}

@test "reposcript hook: allows the relative form of a top-level bin script" {
  run _run_guard '{"tool_input":{"command":"bin/otto-workbench sync"}}'
  [ "$status" -eq 0 ]
}

@test "reposcript hook: allows an absolute path inside a quoted argument" {
  run _run_guard '{"tool_input":{"command":"git commit -m \"drop; /Users/me/repo/bin/local/old\""}}'
  [ "$status" -eq 0 ]
}

# A bare `bin/` is claimed only behind a `./`. Absolutely spelled it belongs to
# whatever tree the path names, and a virtualenv ships one — suggesting
# `bin/pip` there would send Claude at a script that does not exist.
@test "reposcript hook: leaves an absolute virtualenv bin path alone" {
  run _run_guard '{"tool_input":{"command":"/tmp/fonttools-venv/bin/pip install fonttools"}}'
  [ "$status" -eq 0 ]
}

@test "reposcript hook: defers a PATH bin dir to the bare-name rule" {
  run _run_guard '{"tool_input":{"command":"/opt/homebrew/bin/gh pr view 42"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'gh'"* ]]
}

@test "pathbin hook: blocks /bin/cat and names the bare command" {
  run _run_guard '{"tool_input":{"command":"/bin/cat /tmp/x/review.diff"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'cat'"* ]]
}

@test "pathbin hook: blocks /usr/bin after a statement separator" {
  run _run_guard '{"tool_input":{"command":"ls -la; /usr/bin/grep -n foo f"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'grep'"* ]]
}

@test "pathbin hook: blocks /bin with no space after the separator" {
  run _run_guard '{"tool_input":{"command":"ls -la;/bin/cat f"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'cat'"* ]]
}

@test "pathbin hook: blocks a ~/.local/bin path and names the bare command" {
  run _run_guard '{"tool_input":{"command":"/Users/me/.local/bin/mise doctor"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'mise'"* ]]
  [[ "$output" == *"/Users/me/.local/bin/"* ]]
}

@test "pathbin hook: blocks a ~/.local/bin path after a statement separator" {
  run _run_guard '{"tool_input":{"command":"ls -la; /Users/me/.local/bin/rtk read f"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'rtk'"* ]]
}

@test "pathbin hook: blocks a homebrew path and names the bare command" {
  run _run_guard '{"tool_input":{"command":"/opt/homebrew/bin/gh pr view 42"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'gh'"* ]]
}

@test "pathbin hook: blocks /usr/local/bin and names the bare command" {
  run _run_guard '{"tool_input":{"command":"/usr/local/bin/node --version"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'node'"* ]]
}

@test "pathbin hook: blocks a mise install bin and names the bare command" {
  run _run_guard '{"tool_input":{"command":"/Users/me/.local/share/mise/installs/node/24.18.1/bin/node --test a.mjs"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'node'"* ]]
}

@test "pathbin hook: blocks a mise shim path" {
  run _run_guard '{"tool_input":{"command":"/Users/me/.local/share/mise/shims/node --version"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'node'"* ]]
}

@test "pathbin hook: blocks an asdf shim path" {
  run _run_guard '{"tool_input":{"command":"/Users/me/.asdf/shims/python --version"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'python'"* ]]
}

@test "pathbin hook: allows a mise install path as an argument" {
  run _run_guard '{"tool_input":{"command":"rtk ls /Users/me/.local/share/mise/installs/node/24.18.1/bin/"}}'
  [ "$status" -eq 0 ]
}

@test "pathbin hook: allows the bare command name" {
  run _run_guard '{"tool_input":{"command":"cat /tmp/x/review.diff"}}'
  [ "$status" -eq 0 ]
}

@test "pathbin hook: allows a /bin path inside a sed expression" {
  run _run_guard "{\"tool_input\":{\"command\":\"sed -e 's|/bin/cat|x|' f\"}}"
  [ "$status" -eq 0 ]
}

@test "pathbin hook: allows absolute paths outside every PATH bin dir" {
  run _run_guard '{"tool_input":{"command":"/Users/me/git/repo/scripts/thing"}}'
  [ "$status" -eq 0 ]
}

@test "pathbin hook: allows a PATH bin path that is an argument, not the command" {
  run _run_guard '{"tool_input":{"command":"ls -la /Users/me/.local/bin/mise"}}'
  [ "$status" -eq 0 ]
}

@test "pathbin hook: allows a separator-prefixed path inside a quoted argument" {
  run _run_guard '{"tool_input":{"command":"git commit -m \"fix: drop; /usr/bin/env callers\""}}'
  [ "$status" -eq 0 ]
}

# ── statement-anchored Bash guardrails ──────────────────────────────────────
# These checks match at the start of any statement, not just the start of the
# command — a leading no-op token must not be a way around them, and neither is
# a second line. Heredoc bodies are dropped before any rule runs, so content
# being written to a file is not scanned as if it were the command itself. The
# quote-stripping ceiling is documented in the guard script itself.

@test "funcdef hook: blocks a cd() no-op stub wrapping a grep" {
  run _run_guard '{"tool_input":{"command":"cd() { :; }; W=/tmp/x; grep -rn foo \"$W/tests/\""}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"function_definition"* ]]
}

@test "funcdef hook: blocks the function keyword form" {
  run _run_guard '{"tool_input":{"command":"function run { echo hi; }; run"}}'
  [ "$status" -eq 2 ]
}

@test "funcdef hook: allows a plain grep with parens inside quotes" {
  run _run_guard "{\"tool_input\":{\"command\":\"grep -rnE '(worktree list|worktree_list)' /tmp/x/tests/ | head -40\"}}"
  [ "$status" -eq 0 ]
}

@test "var hook: blocks a VAR=value prefix at the start" {
  run _run_guard '{"tool_input":{"command":"W=/tmp/x grep -rn foo /tmp/x"}}'
  [ "$status" -eq 2 ]
}

@test "var hook: blocks a VAR=value assignment after a leading token" {
  run _run_guard '{"tool_input":{"command":"true; W=/tmp/x; grep -rn foo /tmp/x"}}'
  [ "$status" -eq 2 ]
}

@test "var hook: allows uppercase flag values mid-command" {
  run _run_guard '{"tool_input":{"command":"docker run -e FOO=bar alpine"}}'
  [ "$status" -eq 0 ]
}

# ── multi-line scanning ─────────────────────────────────────────────────────
# A multi-line command is several commands, and the second prompts as loudly as
# the first. Lines are joined with `; ` before the anchored rules run, which is
# why a lone `cd <dir>` — the sanctioned form — still has no separator to match.

@test "scan: blocks a pathbin invocation on a later line" {
  run _run_guard '{"tool_input":{"command":"rtk ls /tmp/x\n/Users/me/.local/share/mise/installs/node/24.18.1/bin/node --test a.mjs"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Use 'node'"* ]]
}

@test "scan: blocks an env-var prefix on a later line" {
  run _run_guard '{"tool_input":{"command":"rtk ls /tmp/x\nNODEBIN=/tmp/x/node\ntrue"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"VAR=value"* ]]
}

@test "scan: does not end a plain heredoc on an indented marker word" {
  run _run_guard '{"tool_input":{"command":"cat > /tmp/x/run.sh <<EOF\n  EOF\ntrue; FOO=bar\nEOF"}}'
  [ "$status" -eq 0 ]
}

@test "scan: ends a dash heredoc on an indented marker" {
  run _run_guard '{"tool_input":{"command":"cat > /tmp/x/run.sh <<-EOF\nbody\n  EOF\ntrue; FOO=bar"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"VAR=value"* ]]
}

@test "scan: still allows a bare cd as its own call" {
  run _run_guard '{"tool_input":{"command":"cd /Users/me/git/repo"}}'
  [ "$status" -eq 0 ]
}

@test "scan: treats a following line as a compound cd" {
  run _run_guard '{"tool_input":{"command":"cd /tmp/x\nls -la"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Compound cd"* ]]
}

# ── sh -c wrappers ──────────────────────────────────────────────────────────
# The analyzer cannot see inside the quoted payload, so it prompts for the
# wrapper as a whole and the offered rule keys on that exact string. The guard
# cannot see inside either — double-quoted spans are stripped before it runs.

@test "dashc hook: blocks sh -c wrapping a compound cd" {
  run _run_guard '{"tool_input":{"command":"sh -c \"cd /tmp/x; node --test a.mjs\""}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"sh -c"* ]]
}

@test "dashc hook: blocks bash -c on a later line" {
  run _run_guard '{"tool_input":{"command":"rtk ls /tmp/x\nbash -c \"ls -la\""}}'
  [ "$status" -eq 2 ]
}

@test "dashc hook: blocks a combined flag form" {
  run _run_guard '{"tool_input":{"command":"sh -ec \"ls\""}}'
  [ "$status" -eq 2 ]
}

@test "dashc hook: blocks a path-prefixed shell and names the wrapper" {
  run _run_guard '{"tool_input":{"command":"/bin/sh -c \"ls -la\""}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"sh -c"* ]]
}

@test "dashc hook: allows running a shell on a script file" {
  run _run_guard '{"tool_input":{"command":"bash /tmp/x/probe.sh"}}'
  [ "$status" -eq 0 ]
}

@test "dashc hook: allows a command merely containing sh" {
  run _run_guard '{"tool_input":{"command":"shellcheck -c /tmp/x/probe.sh"}}'
  [ "$status" -eq 0 ]
}
