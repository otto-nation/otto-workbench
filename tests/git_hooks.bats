#!/usr/bin/env bats
# Tests for the git hooks: global and local hooks, the commit identity guard, worktrunk pre-switch.

setup() {
  load 'test_helper'
  common_setup
  ORIG_DIR="$PWD"

  # Source steps.sh for access to helper functions
  . "$REPO_ROOT/lib/ui.sh"
  . "$REPO_ROOT/git/steps.sh"
}

teardown() {
  cd "$ORIG_DIR" || return 1
  common_teardown
}

# ── Hooks ────────────────────────────────────────────────────────────────────

@test "pre-commit hook source exists" {
  [ -f "$GIT_HOOKS_SRC_DIR/pre-commit" ]
}

@test "pre-push hook source exists" {
  [ -f "$GIT_HOOKS_SRC_DIR/pre-push" ]
}

@test "workbench hooks exist" {
  [ -f "$GIT_HOOKS_SRC_DIR/pre-commit-workbench" ]
  [ -f "$GIT_HOOKS_SRC_DIR/pre-push-workbench" ]
}

@test "global hooks install every hook the workbench ships, pointing at the checkout" {
  GIT_HOOKS_DIR="$TMPDIR/git-hooks"
  # install_symlink re-points a worktree's links at the stable checkout; pinned
  # equal so the links name this checkout on any machine layout.
  WORKBENCH_STABLE_DIR="$WORKBENCH_DIR" run step_global_hooks
  [ "$status" -eq 0 ]
  local hook
  for hook in pre-commit prepare-commit-msg pre-push post-rewrite; do
    [ "$(readlink "$GIT_HOOKS_DIR/$hook")" = "$GIT_HOOKS_SRC_DIR/$hook" ]
    [ -x "$GIT_HOOKS_SRC_DIR/$hook" ]
  done
  [ "$(git config --global core.hooksPath)" = "$GIT_HOOKS_DIR" ]
}

@test "pre-commit hook has current header" {
  grep -q "git/steps.sh" "$GIT_HOOKS_SRC_DIR/pre-commit"
  run grep "task dev:setup" "$GIT_HOOKS_SRC_DIR/pre-commit"
  [ "$status" -ne 0 ]
}

@test "pre-push hook has current header" {
  run grep "task dev:setup" "$GIT_HOOKS_SRC_DIR/pre-push"
  [ "$status" -ne 0 ]
}

# ── Local hooks ──────────────────────────────────────────────────────────────
#
# Every case here runs step_local_hooks from a directory that is not the
# checkout it is installing into, because that is the only position where the
# lookup's answer can be wrong: on this machine the workbench is a bare-repo
# container, whose --git-common-dir is absolute whoever asks.

@test "local hooks land in the checkout's git dir, not the caller's cwd" {
  # `git -C DIR rev-parse --git-common-dir` reports a plain `.git` for an
  # ordinary clone, and that resolves against the caller's cwd rather than DIR.
  local repo="$TMPDIR/checkout" elsewhere="$TMPDIR/elsewhere"
  git init -q "$repo"
  mkdir -p "$elsewhere"
  cd "$elsewhere" || return 1

  WORKBENCH_DIR="$repo" run step_local_hooks
  [ "$status" -eq 0 ]
  [ -x "$repo/.git/hooks/pre-commit" ]
  [ -x "$repo/.git/hooks/pre-push" ]
  [ ! -e "$elsewhere/.git" ]
}

@test "local hooks of a worktree land in the git dir its checkouts share" {
  local repo="$TMPDIR/checkout"
  git init -q "$repo"
  git -C "$repo" -c user.name=Test -c user.email=test@example.com \
    commit -q --allow-empty -m base
  git -C "$repo" worktree add -q "$TMPDIR/wt" -b feat

  WORKBENCH_DIR="$TMPDIR/wt" run step_local_hooks
  [ "$status" -eq 0 ]
  [ -x "$repo/.git/hooks/pre-commit" ]
  [ ! -e "$TMPDIR/wt/.git/hooks" ]
}

@test "an inherited GIT_DIR does not redirect the local hook install" {
  # Sync runs from inside a git hook, which exports GIT_DIR — and git reads it
  # ahead of the directory `-C` names, so the dispatchers would be written into
  # the hook's repository and the answer would look entirely ordinary.
  local repo="$TMPDIR/checkout" other="$TMPDIR/other"
  git init -q "$repo"
  git init -q "$other"

  GIT_DIR="$other/.git" WORKBENCH_DIR="$repo" run step_local_hooks
  [ "$status" -eq 0 ]
  [ -x "$repo/.git/hooks/pre-commit" ]
  [ ! -e "$other/.git/hooks/pre-commit" ]
}

@test "the disabling hooksPath is cleared in the checkout, not the caller's repo" {
  local repo="$TMPDIR/checkout" elsewhere="$TMPDIR/elsewhere"
  git init -q "$repo"
  git init -q "$elsewhere"
  git -C "$repo" config --local core.hooksPath /dev/null
  git -C "$elsewhere" config --local core.hooksPath /dev/null
  cd "$elsewhere" || return 1

  WORKBENCH_DIR="$repo" run step_local_hooks
  [ "$status" -eq 0 ]
  run git -C "$repo" config --local core.hooksPath
  [ "$status" -ne 0 ]
  [ "$(git -C "$elsewhere" config --local core.hooksPath)" = "/dev/null" ]
}

@test "a directory git cannot answer for fails the step instead of installing" {
  mkdir -p "$TMPDIR/loose"

  WORKBENCH_DIR="$TMPDIR/loose" run step_local_hooks
  [ "$status" -ne 0 ]
  [[ "$output" == *"names no git dir"* ]]
}

# ── Commit identity guard ────────────────────────────────────────────────────

# _make_identity_repo NAME EMAIL [ORIGIN] — a staged temp repo committing as
# NAME <EMAIL>. Omit ORIGIN for a repo with no remote.
_make_identity_repo() {
  local name="$1" email="$2" origin="${3:-}"
  local dir="$TMPDIR/identity-repo"

  git init -q "$dir"
  [[ -z "$origin" ]] || git -C "$dir" remote add origin "$origin"
  git -C "$dir" config user.name "$name"
  git -C "$dir" config user.email "$email"
  echo "hello" > "$dir/file.txt"
  git -C "$dir" add file.txt

  cd "$dir" || return 1
  _assert_not_real_repo || return 1
}

# _refute_identity_rejection — the guard let the commit through.
#
# Past the guard the hook runs gitleaks, which is not installed on CI, so a
# non-zero exit there is not a guard failure. Assert the run reached that stage.
_refute_identity_rejection() {
  [[ "$output" != *"placeholder identity"* ]] || return 1
  if command -v gitleaks >/dev/null 2>&1; then
    [ "$status" -eq 0 ]
  else
    [[ "$output" == *"gitleaks not found"* ]]
  fi
}

@test "pre-commit rejects a placeholder email on a forge remote" {
  _make_identity_repo "Test" "test@test.com" "git@github.com:owner/repo.git"

  run "$GIT_HOOKS_SRC_DIR/pre-commit"

  [ "$status" -eq 1 ]
  [[ "$output" == *"placeholder identity — email=test@test.com, name=Test"* ]]
}

@test "pre-commit rejects a placeholder name alongside a real email" {
  _make_identity_repo "Test" "someone@company.com" "https://github.com/owner/repo.git"

  run "$GIT_HOOKS_SRC_DIR/pre-commit"

  [ "$status" -eq 1 ]
  # Only the name is flagged — the email is real.
  [[ "$output" == *"placeholder identity — name=Test"* ]]
}

@test "pre-commit rejects a placeholder identity from the environment" {
  _make_identity_repo "Real Person" "real@users.noreply.github.com" \
    "git@github.com:owner/repo.git"

  run env GIT_AUTHOR_EMAIL="test@test.com" "$GIT_HOOKS_SRC_DIR/pre-commit"

  [ "$status" -eq 1 ]
  [[ "$output" == *"placeholder identity — email=test@test.com"* ]]
}

@test "pre-commit allows a placeholder identity when there is no forge remote" {
  _make_identity_repo "Test" "test@test.com"

  run "$GIT_HOOKS_SRC_DIR/pre-commit"

  _refute_identity_rejection
}

@test "pre-commit allows a real identity on a forge remote" {
  _make_identity_repo "Real Person" "real@users.noreply.github.com" \
    "git@github.com:owner/repo.git"

  run "$GIT_HOOKS_SRC_DIR/pre-commit"

  _refute_identity_rejection
}

@test "pre-commit rejects a placeholder identity on an enterprise remote" {
  # The forge list named github.com, gitlab.com and bitbucket.org, so a GitHub
  # Enterprise remote skipped the check entirely — the guard was absent on
  # exactly the remotes a company runs.
  _make_identity_repo "Test" "test@test.com" "git@ghe.acme.com:owner/repo.git"

  run "$GIT_HOOKS_SRC_DIR/pre-commit"

  [ "$status" -eq 1 ]
  [[ "$output" == *"placeholder identity — email=test@test.com, name=Test"* ]]
}

@test "pre-commit rejects a placeholder identity on a self-hosted HTTPS remote" {
  _make_identity_repo "Test" "test@test.com" "https://gitlab.internal.acme/owner/repo.git"

  run "$GIT_HOOKS_SRC_DIR/pre-commit"

  [ "$status" -eq 1 ]
  [[ "$output" == *"placeholder identity"* ]]
}

# passes-at-base: listed by the old glob; proves the check is a superset
@test "pre-commit rejects a placeholder identity on a gitlab.com remote" {
  # Listed by the old glob and still guarded — the structural check is a
  # superset of the three hosts it replaced, not a swap.
  _make_identity_repo "Test" "test@test.com" "git@gitlab.com:owner/repo.git"

  run "$GIT_HOOKS_SRC_DIR/pre-commit"

  [ "$status" -eq 1 ]
  [[ "$output" == *"placeholder identity"* ]]
}

# passes-at-base: the exemption the widening must not swallow
@test "pre-commit allows a placeholder identity on a local path remote" {
  # A throwaway repo cloned from a path on disk is what the exemption is for,
  # and the structural check must not widen far enough to catch it.
  _make_identity_repo "Test" "test@test.com" "/srv/git/repo.git"

  run "$GIT_HOOKS_SRC_DIR/pre-commit"

  _refute_identity_rejection
}

# passes-at-base: the exemption the widening must not swallow
@test "pre-commit allows a placeholder identity on a file:// remote" {
  _make_identity_repo "Test" "test@test.com" "file:///srv/git/repo.git"

  run "$GIT_HOOKS_SRC_DIR/pre-commit"

  _refute_identity_rejection
}

# ── Repo-local delegation ────────────────────────────────────────────────────

@test "pre-commit finds the local hook through the environment git exported" {
  # `git --git-dir=X --work-tree=Y commit` runs the hook in Y with GIT_DIR set
  # to X, and Y is no repository on its own. Clearing the environment and
  # discovering from Y — what lib/git_layout.sh's git_shared_dir does, and the
  # reason a hook does not call it — finds no repository at all, or worse
  # whichever one happens to enclose Y.
  if ! command -v gitleaks >/dev/null 2>&1; then
    bats_skip "gitleaks not installed — the hook exits before it reaches delegation"
  fi
  local gitdir="$TMPDIR/store/repo.git" tree="$TMPDIR/tree"
  mkdir -p "$TMPDIR/store" "$tree"
  git init -q --bare "$gitdir"
  printf '#!/usr/bin/env bash\necho "LOCAL delegated"\n' > "$gitdir/hooks/pre-commit"
  chmod +x "$gitdir/hooks/pre-commit"
  printf 'hello\n' > "$tree/file.txt"
  cd "$tree" || return 1
  GIT_DIR="$gitdir" GIT_WORK_TREE="$tree" git add file.txt

  GIT_DIR="$gitdir" GIT_WORK_TREE="$tree" run "$GIT_HOOKS_SRC_DIR/pre-commit"

  [ "$status" -eq 0 ]
  [[ "$output" == *"LOCAL delegated"* ]]
}

@test "all git hooks use portable shebang" {
  local bad=()
  for hook in "$GIT_HOOKS_SRC_DIR"/*; do
    [ -f "$hook" ] || continue
    local first_line
    first_line="$(head -1 "$hook")"
    # Only check files that have a bash shebang at all
    if [[ "$first_line" == *"bash"* ]] && [[ "$first_line" != "#!/usr/bin/env bash" ]]; then
      bad+=("$(basename "$hook")")
    fi
  done
  if [ "${#bad[@]}" -gt 0 ]; then
    echo "hooks with wrong shebang (expected #!/usr/bin/env bash): ${bad[*]}"
    return 1
  fi
}

# ── Multi-identity helpers ──────────────────────────────────────────────────

@test "write_identity_config creates identity file with user section" {
  GIT_IDENTITY_DIR="$TMPDIR/identities"

  local result
  result="$(_git_write_identity_config "work" "Work User" "work@company.com" "ABCD1234")"

  [ -f "$result" ]
  grep -q 'name = Work User' "$result"
  grep -q 'email = work@company.com' "$result"
  grep -q 'signingKey = ABCD1234' "$result"
}

@test "write_identity_config omits signingKey when empty" {
  GIT_IDENTITY_DIR="$TMPDIR/identities"

  local result
  result="$(_git_write_identity_config "personal" "Personal User" "me@home.com")"

  [ -f "$result" ]
  grep -q 'name = Personal User' "$result"
  grep -q 'email = me@home.com' "$result"
  run grep 'signingKey' "$result"
  [ "$status" -ne 0 ]
}

@test "write_identity_config creates identity directory" {
  GIT_IDENTITY_DIR="$TMPDIR/new-dir/identities"

  _git_write_identity_config "test" "Test" "test@test.com" > /dev/null

  [ -d "$GIT_IDENTITY_DIR" ]
}

@test "ensure_includeif adds stanza for directory" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  echo "[user]" > "$fake_gitconfig"
  GITCONFIG_FILE="$fake_gitconfig"

  _gitconfig_ensure_includeif "$HOME/git/work" "/path/to/work.gitconfig"

  grep -q 'includeIf "gitdir:'"$HOME"'/git/work/"' "$fake_gitconfig"
  grep -q 'path = /path/to/work.gitconfig' "$fake_gitconfig"
}

@test "ensure_includeif is idempotent" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  echo "[user]" > "$fake_gitconfig"
  GITCONFIG_FILE="$fake_gitconfig"

  _gitconfig_ensure_includeif "$HOME/git/work/" "/path/to/work.gitconfig"
  _gitconfig_ensure_includeif "$HOME/git/work/" "/path/to/work.gitconfig"

  local count
  count=$(grep -c 'includeIf' "$fake_gitconfig")
  [ "$count" -eq 1 ]
}

@test "ensure_includeif normalizes trailing slash" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  echo "[user]" > "$fake_gitconfig"
  GITCONFIG_FILE="$fake_gitconfig"

  # Pass without trailing slash
  _gitconfig_ensure_includeif "$HOME/git/work" "/path/to/work.gitconfig"

  # Should have trailing slash in the gitdir pattern
  grep -q 'gitdir:'"$HOME"'/git/work/' "$fake_gitconfig"
}

@test "apply_template creates gitconfig with template content" {
  GITCONFIG_FILE="$TMPDIR/.gitconfig"

  _gitconfig_apply_template

  [ -f "$GITCONFIG_FILE" ]
  grep -q '\[user\]' "$GITCONFIG_FILE"
}

@test "set_default_identity substitutes placeholders" {
  GITCONFIG_FILE="$TMPDIR/.gitconfig"
  cp "$GIT_CONFIG_TEMPLATE" "$GITCONFIG_FILE"

  _gitconfig_set_default_identity "Test User" "test@example.com" "KEY123"

  grep -q 'name = Test User' "$GITCONFIG_FILE"
  grep -q 'email = test@example.com' "$GITCONFIG_FILE"
  grep -q 'signingKey = KEY123' "$GITCONFIG_FILE"
}

@test "template documents multi-identity pattern" {
  grep -q 'includeIf' "$GIT_CONFIG_TEMPLATE"
  grep -q 'identities' "$GIT_CONFIG_TEMPLATE"
}

# ── worktrunk pre-switch hook ────────────────────────────────────────────────

# The line the step installs. Spelled out here rather than read from the step,
# so a change to the template has to be made in both places on purpose.
WORKTRUNK_HOOK_LINE='fetch-default = "wt-fetch-default {{ default_branch }}"'

# The line issue #936 was about: `worktree_path_of_branch` renders empty when no
# worktree holds the default branch, `git -C ''` runs in the caller's working
# directory instead, and `|| true` hides whatever it did there.
WORKTRUNK_STALE_LINE='fetch-default = "git fetch origin {{ default_branch }} && git -C {{ worktree_path_of_branch(default_branch) }} merge --ff-only origin/{{ default_branch }} || true"'

# _worktrunk_setup — point the step at a scratch config, and put a `wt` on PATH
# because the step backs off entirely when worktrunk is not installed.
_worktrunk_setup() {
  WORKTRUNK_CONFIG_FILE="$TMPDIR/worktrunk/config.toml"
  mkdir -p "$TMPDIR/worktrunk" "$TMPDIR/bin"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$TMPDIR/bin/wt"
  chmod +x "$TMPDIR/bin/wt"
  PATH="$TMPDIR/bin:$PATH"
}

@test "pre_switch adds the hook and its section to a config with neither" {
  _worktrunk_setup
  printf 'worktree-path = "{{ repo_path }}/../{{ branch | sanitize }}"\n' > "$WORKTRUNK_CONFIG_FILE"

  step_worktrunk_pre_switch_fetch

  grep -q '^\[pre-switch\]' "$WORKTRUNK_CONFIG_FILE"
  grep -qxF "$WORKTRUNK_HOOK_LINE" "$WORKTRUNK_CONFIG_FILE"
  grep -q '^worktree-path' "$WORKTRUNK_CONFIG_FILE"
}

@test "pre_switch appends under an existing pre-switch section" {
  _worktrunk_setup
  printf '[pre-switch]\nother = "true"\n' > "$WORKTRUNK_CONFIG_FILE"

  step_worktrunk_pre_switch_fetch

  grep -qxF "$WORKTRUNK_HOOK_LINE" "$WORKTRUNK_CONFIG_FILE"
  grep -q '^other = "true"' "$WORKTRUNK_CONFIG_FILE"
  [ "$(grep -c '^\[pre-switch\]' "$WORKTRUNK_CONFIG_FILE")" -eq 1 ]
}

@test "pre_switch is idempotent" {
  _worktrunk_setup
  : > "$WORKTRUNK_CONFIG_FILE"

  step_worktrunk_pre_switch_fetch
  step_worktrunk_pre_switch_fetch

  [ "$(grep -c '^fetch-default' "$WORKTRUNK_CONFIG_FILE")" -eq 1 ]
}

@test "pre_switch rewrites the hook that no-ops in bare layouts" {
  _worktrunk_setup
  printf '[pre-switch]\n%s\n' "$WORKTRUNK_STALE_LINE" > "$WORKTRUNK_CONFIG_FILE"

  step_worktrunk_pre_switch_fetch

  grep -qxF "$WORKTRUNK_HOOK_LINE" "$WORKTRUNK_CONFIG_FILE"
  run grep -q 'worktree_path_of_branch' "$WORKTRUNK_CONFIG_FILE"
  [ "$status" -ne 0 ]
  [ "$(grep -c '^fetch-default' "$WORKTRUNK_CONFIG_FILE")" -eq 1 ]
}

@test "pre_switch refresh keeps the rest of the config" {
  _worktrunk_setup
  printf 'worktree-path = "custom"\n\n[pre-switch]\n%s\nother = "true"\n' \
    "$WORKTRUNK_STALE_LINE" > "$WORKTRUNK_CONFIG_FILE"

  step_worktrunk_pre_switch_fetch

  grep -q '^worktree-path = "custom"' "$WORKTRUNK_CONFIG_FILE"
  grep -q '^other = "true"' "$WORKTRUNK_CONFIG_FILE"
  grep -qxF "$WORKTRUNK_HOOK_LINE" "$WORKTRUNK_CONFIG_FILE"
}

@test "pre_switch skips when the worktrunk config is missing" {
  _worktrunk_setup
  rm -f "$WORKTRUNK_CONFIG_FILE"

  run step_worktrunk_pre_switch_fetch

  [ "$status" -eq 0 ]
  [ ! -f "$WORKTRUNK_CONFIG_FILE" ]
}

@test "pre_switch installs a command that exists" {
  _worktrunk_setup
  : > "$WORKTRUNK_CONFIG_FILE"

  step_worktrunk_pre_switch_fetch

  local command_name
  command_name=$(sed -n 's/^fetch-default = "\([^ ]*\).*/\1/p' "$WORKTRUNK_CONFIG_FILE")
  [ -x "$REPO_ROOT/git/bin/$command_name" ]
}
