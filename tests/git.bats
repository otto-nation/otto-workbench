#!/usr/bin/env bats
# Tests for the git configuration setup (2-layer architecture).

setup() {
  load 'test_helper'
  common_setup
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"

  # Source steps.sh for access to helper functions
  . "$REPO_ROOT/lib/ui.sh"
  . "$REPO_ROOT/git/steps.sh"
}

teardown() {
  cd "$ORIG_DIR" || return 1
  rm -rf "$TMPDIR"
  common_teardown
}

# ── Bootstrap ────────────────────────────────────────────────────────────────

@test "bootstrap creates gitconfig from template when missing" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  GITCONFIG_FILE="$fake_gitconfig"

  _gitconfig_bootstrap

  [ -f "$fake_gitconfig" ]
  grep -q '\[user\]' "$fake_gitconfig"
}

@test "bootstrap does not overwrite existing gitconfig" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  echo "existing content" > "$fake_gitconfig"
  GITCONFIG_FILE="$fake_gitconfig"

  _gitconfig_bootstrap

  grep -q "existing content" "$fake_gitconfig"
}

# ── Include stanza ───────────────────────────────────────────────────────────

@test "ensure_include adds shared config include when missing" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  echo "[user]" > "$fake_gitconfig"
  GITCONFIG_FILE="$fake_gitconfig"

  _gitconfig_ensure_include "/some/path/gitconfig.shared"

  grep -q "path = /some/path/gitconfig.shared" "$fake_gitconfig"
}

@test "ensure_include is idempotent" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  printf '[include]\n\tpath = /some/path/gitconfig.shared\n' > "$fake_gitconfig"
  GITCONFIG_FILE="$fake_gitconfig"

  _gitconfig_ensure_include "/some/path/gitconfig.shared"

  local count
  count=$(grep -c "path = /some/path/gitconfig.shared" "$fake_gitconfig")
  [ "$count" -eq 1 ]
}

@test "ensure_include preserves existing content" {
  local fake_gitconfig="$TMPDIR/.gitconfig"
  cat > "$fake_gitconfig" <<'EOF'
[user]
	name = Test User
	email = test@example.com
EOF
  GITCONFIG_FILE="$fake_gitconfig"

  _gitconfig_ensure_include "/some/path/gitconfig.shared"

  grep -q "name = Test User" "$fake_gitconfig"
  grep -q "path = /some/path/gitconfig.shared" "$fake_gitconfig"
}

# ── Architecture (live machine) ──────────────────────────────────────────────

@test "shared config file exists and is non-empty" {
  [ -f "$GIT_SHARED_CONFIG" ]
  [ -s "$GIT_SHARED_CONFIG" ]
}

@test "shared config has documentation header" {
  grep -q "Architecture:" "$GIT_SHARED_CONFIG"
}

@test "shared config does not contain machine-specific sections" {
  run grep '^\[user\]' "$GIT_SHARED_CONFIG"
  [ "$status" -ne 0 ]
  run grep '^\[credential\]' "$GIT_SHARED_CONFIG"
  [ "$status" -ne 0 ]
}

# ── Template ─────────────────────────────────────────────────────────────────

@test "gitconfig template exists and is non-empty" {
  [ -f "$GIT_CONFIG_TEMPLATE" ]
  [ -s "$GIT_CONFIG_TEMPLATE" ]
}

@test "template contains user section" {
  grep -q '\[user\]' "$GIT_CONFIG_TEMPLATE"
}

@test "template contains gpg section" {
  grep -q '\[gpg\]' "$GIT_CONFIG_TEMPLATE"
}

@test "template contains credential section" {
  grep -q '\[credential\]' "$GIT_CONFIG_TEMPLATE"
}

@test "template documents the 2-layer architecture" {
  grep -q 'gitconfig.shared' "$GIT_CONFIG_TEMPLATE"
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

# ── GitHub SSH ──────────────────────────────────────────────────────────────

# The stand-in host key the known_hosts tests copy and look for. Real in shape
# and nothing else — the step never verifies a key, it moves the ones already
# trusted under a second name.
SSH_GITHUB_FAKE_KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAFAKEKEY"

# _ssh_github_setup [KEY_VALUE] — point the step at a scratch ssh directory
# standing in for ~/.ssh, and a temp global config holding KEY_VALUE, from a
# directory that is not a git repo.
#
# The config root is exported rather than the file path overridden, because
# wb_config_get resolves the scopes in a child process now — lib/config_cli.py,
# which builds the global path from WORKBENCH_CONFIG_DIR the same way
# constants.sh does. A WORKBENCH_CONFIG_FILE set only in this shell would leave
# that child reading the real one.
#
# The cd matters for the same reason it always did: the project scope resolves
# from the working directory, and inside this repo that is a .workbench.yml the
# test does not control.
_ssh_github_setup() {
  SSH_DIR="$TMPDIR/ssh"
  SSH_CONFIG_FILE="$SSH_DIR/config"
  SSH_KNOWN_HOSTS_FILE="$SSH_DIR/known_hosts"
  mkdir -p "$SSH_DIR"

  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  mkdir -p "$WORKBENCH_CONFIG_DIR"
  WORKBENCH_CONFIG_FILE="$WORKBENCH_CONFIG_DIR/$WORKBENCH_CONFIG_NAME"
  if [[ -n "${1:-}" ]]; then
    printf 'github:\n  ssh_over_443: %s\n' "$1" > "$WORKBENCH_CONFIG_FILE"
  fi

  mkdir -p "$TMPDIR/not-a-repo"
  cd "$TMPDIR/not-a-repo" || return 1
}

# _assert_keepalive_present — fail unless SSH_CONFIG_FILE carries both
# keepalive lines the managed block always writes.
_assert_keepalive_present() {
  grep -q '  ServerAliveInterval 30' "$SSH_CONFIG_FILE"
  grep -q '  ServerAliveCountMax 10' "$SSH_CONFIG_FILE"
}

# _ssh_github_user_config — an ~/.ssh/config shaped like a real one: includes that
# must stay at the top, then a catch-all Host block.
_ssh_github_user_config() {
  cat > "$SSH_CONFIG_FILE" <<'EOF'
Include ~/.orbstack/ssh/config

Host myserver
  User me

Host *
  UseKeychain yes
EOF
}

@test "github ssh block lands ahead of the first Host block" {
  _ssh_github_setup true
  _ssh_github_user_config

  step_github_ssh

  local block_line host_line
  block_line=$(grep -n 'Hostname ssh.github.com' "$SSH_CONFIG_FILE" | cut -d: -f1)
  host_line=$(grep -n '^Host myserver' "$SSH_CONFIG_FILE" | cut -d: -f1)
  [ "$block_line" -lt "$host_line" ]
}

@test "github ssh block lands after the Include lines" {
  _ssh_github_setup true
  _ssh_github_user_config

  step_github_ssh

  local include_line block_line
  include_line=$(grep -n '^Include' "$SSH_CONFIG_FILE" | cut -d: -f1)
  block_line=$(grep -n 'Hostname ssh.github.com' "$SSH_CONFIG_FILE" | cut -d: -f1)
  [ "$include_line" -lt "$block_line" ]
}

@test "github ssh preserves the user's own entries" {
  _ssh_github_setup true
  _ssh_github_user_config

  step_github_ssh

  grep -q '^Include ~/.orbstack/ssh/config' "$SSH_CONFIG_FILE"
  grep -q '^Host myserver' "$SSH_CONFIG_FILE"
  grep -q '  UseKeychain yes' "$SSH_CONFIG_FILE"
}

@test "github ssh appends when the config has no Host block" {
  _ssh_github_setup true
  printf 'Include ~/.colima/ssh_config\n' > "$SSH_CONFIG_FILE"

  step_github_ssh

  grep -q '^Include ~/.colima/ssh_config' "$SSH_CONFIG_FILE"
  grep -q 'Hostname ssh.github.com' "$SSH_CONFIG_FILE"
}

@test "github ssh creates the config when there is none" {
  _ssh_github_setup true

  step_github_ssh

  [ -f "$SSH_CONFIG_FILE" ]
  grep -q 'Port 443' "$SSH_CONFIG_FILE"
}

@test "github ssh is idempotent" {
  _ssh_github_setup true
  _ssh_github_user_config

  step_github_ssh
  step_github_ssh

  local count
  count=$(grep -c 'Hostname ssh.github.com' "$SSH_CONFIG_FILE")
  [ "$count" -eq 1 ]
}

@test "github ssh keeps the direct route when the key is unset" {
  _ssh_github_setup
  _ssh_github_user_config

  step_github_ssh

  run grep 'ssh.github.com' "$SSH_CONFIG_FILE"
  [ "$status" -ne 0 ]
  grep -q '^Host github.com' "$SSH_CONFIG_FILE"
}

@test "github ssh drops the 443 lines when the key flips to false" {
  _ssh_github_setup true
  _ssh_github_user_config
  step_github_ssh

  printf 'github:\n  ssh_over_443: false\n' > "$WORKBENCH_CONFIG_FILE"
  step_github_ssh

  run grep 'ssh.github.com' "$SSH_CONFIG_FILE"
  [ "$status" -ne 0 ]
  run grep 'Port 443' "$SSH_CONFIG_FILE"
  [ "$status" -ne 0 ]
  [ "$(grep -c '^Host github.com' "$SSH_CONFIG_FILE")" -eq 1 ]
}

@test "github ssh keeps the keepalive when the key flips to false" {
  _ssh_github_setup true
  _ssh_github_user_config
  step_github_ssh

  printf 'github:\n  ssh_over_443: false\n' > "$WORKBENCH_CONFIG_FILE"
  step_github_ssh

  _assert_keepalive_present
}

@test "github ssh flipping the key off leaves the rest of the config intact" {
  _ssh_github_setup true
  _ssh_github_user_config
  step_github_ssh

  printf 'github:\n  ssh_over_443: false\n' > "$WORKBENCH_CONFIG_FILE"
  step_github_ssh

  grep -q '^Include ~/.orbstack/ssh/config' "$SSH_CONFIG_FILE"
  grep -q '^Host myserver' "$SSH_CONFIG_FILE"
  grep -q '  UseKeychain yes' "$SSH_CONFIG_FILE"
}

@test "github ssh writes the config with owner-only permissions" {
  _ssh_github_setup true
  _ssh_github_user_config

  step_github_ssh

  [ "$(file_mode "$SSH_CONFIG_FILE")" = "600" ]
}

@test "github ssh copies the github.com host keys under the 443 name" {
  _ssh_github_setup true
  printf 'github.com %s\n' "$SSH_GITHUB_FAKE_KEY" > "$SSH_KNOWN_HOSTS_FILE"

  step_github_ssh

  grep -qxF "[ssh.github.com]:443 $SSH_GITHUB_FAKE_KEY" "$SSH_KNOWN_HOSTS_FILE"
}

@test "github ssh does not duplicate an existing 443 known_hosts entry" {
  _ssh_github_setup true
  {
    printf 'github.com %s\n' "$SSH_GITHUB_FAKE_KEY"
    printf '[ssh.github.com]:443 %s\n' "$SSH_GITHUB_FAKE_KEY"
  } > "$SSH_KNOWN_HOSTS_FILE"

  step_github_ssh

  local count
  count=$(grep -c 'ssh.github.com' "$SSH_KNOWN_HOSTS_FILE")
  [ "$count" -eq 1 ]
}

@test "github ssh warns instead of guessing when github.com is unknown" {
  _ssh_github_setup true
  printf 'gitlab.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAOTHERKEY\n' > "$SSH_KNOWN_HOSTS_FILE"

  run step_github_ssh

  [ "$status" -eq 0 ]
  [[ "$output" == *"no github.com entry"* ]]
  run grep 'ssh.github.com' "$SSH_KNOWN_HOSTS_FILE"
  [ "$status" -ne 0 ]
}

@test "github ssh block lands ahead of a lowercase host block" {
  _ssh_github_setup true
  printf 'host *\n  UseKeychain yes\n' > "$SSH_CONFIG_FILE"

  step_github_ssh

  local block_line wildcard_line
  block_line=$(grep -n 'Hostname ssh.github.com' "$SSH_CONFIG_FILE" | cut -d: -f1)
  wildcard_line=$(grep -n '^host \*' "$SSH_CONFIG_FILE" | cut -d: -f1)
  [ "$block_line" -lt "$wildcard_line" ]
}

@test "github ssh block lands ahead of a leading Match block" {
  _ssh_github_setup true
  printf 'Match host gitlab.com\n  User git\n' > "$SSH_CONFIG_FILE"

  step_github_ssh

  local block_line match_line
  block_line=$(grep -n 'Hostname ssh.github.com' "$SSH_CONFIG_FILE" | cut -d: -f1)
  match_line=$(grep -n '^Match host gitlab.com' "$SSH_CONFIG_FILE" | cut -d: -f1)
  [ "$block_line" -lt "$match_line" ]
}

@test "github ssh rewrites a block whose text has drifted from the template" {
  _ssh_github_setup true
  _ssh_github_user_config
  step_github_ssh

  # Stand in for a machine that installed an older wording of the block.
  perl -pi -e 's/^  Port 443$/  Port 443\n  # stale line from an older release/' \
    "$SSH_CONFIG_FILE"
  step_github_ssh

  run grep 'stale line from an older release' "$SSH_CONFIG_FILE"
  [ "$status" -ne 0 ]
  [ "$(grep -c 'Hostname ssh.github.com' "$SSH_CONFIG_FILE")" -eq 1 ]
  grep -q '^Host myserver' "$SSH_CONFIG_FILE"
}

@test "github ssh refuses to strip a block left open by a hand edit" {
  _ssh_github_setup true
  _ssh_github_user_config
  step_github_ssh

  perl -ni -e 'print unless /^# <<< otto-workbench/' "$SSH_CONFIG_FILE"
  printf 'github:\n  ssh_over_443: false\n' > "$WORKBENCH_CONFIG_FILE"
  run step_github_ssh

  [ "$status" -eq 0 ]
  [[ "$output" == *"no end marker"* ]]
  grep -q '^Host myserver' "$SSH_CONFIG_FILE"
  grep -q '  UseKeychain yes' "$SSH_CONFIG_FILE"
}

@test "github ssh warns when the user declares github.com themselves" {
  _ssh_github_setup true
  printf 'Host github.com\n  User git\n\nHost *\n  UseKeychain yes\n' > "$SSH_CONFIG_FILE"

  run step_github_ssh

  [ "$status" -eq 0 ]
  [[ "$output" == *"outside the managed block"* ]]
}

@test "github ssh stays quiet about github.com when only its own block declares it" {
  _ssh_github_setup true
  _ssh_github_user_config

  run step_github_ssh

  [ "$status" -eq 0 ]
  [[ "$output" != *"outside the managed block"* ]]
}

# ── Keepalive ───────────────────────────────────────────────────────────────

@test "github ssh writes the keepalive with the 443 key off" {
  _ssh_github_setup false
  _ssh_github_user_config

  step_github_ssh

  _assert_keepalive_present
}

@test "github ssh writes the keepalive alongside the 443 routing" {
  _ssh_github_setup true
  _ssh_github_user_config

  step_github_ssh

  grep -q '  Port 443' "$SSH_CONFIG_FILE"
  _assert_keepalive_present
}

@test "github ssh keepalive block lands ahead of the first Host block" {
  _ssh_github_setup false
  _ssh_github_user_config

  step_github_ssh

  local block_line host_line
  block_line=$(grep -n 'ServerAliveInterval' "$SSH_CONFIG_FILE" | cut -d: -f1)
  host_line=$(grep -n '^Host myserver' "$SSH_CONFIG_FILE" | cut -d: -f1)
  [ "$block_line" -lt "$host_line" ]
}

@test "github ssh keepalive leaves no idle gap a pre-push run can outlast" {
  # The failure this guards is a push dropped while pre-push runs: git holds the
  # connection open from before the hook to after it, and the three gates take
  # over five minutes on a developer machine. The interval is the longest the
  # socket goes without traffic, so it is the value that has to stay well inside
  # the remote's idle timeout — a hook of any length is covered as long as the
  # keepalives keep arriving. The count is how many may go unanswered before ssh
  # calls the connection dead, which buys tolerance for a lossy network rather
  # than for a slow hook.
  _ssh_github_setup false

  local interval count
  interval=$(_ssh_github_block false | awk '/ServerAliveInterval/ { print $2 }')
  count=$(_ssh_github_block false | awk '/ServerAliveCountMax/ { print $2 }')

  [ "$interval" -gt 0 ]
  [ "$interval" -le 60 ]
  [ "$count" -ge 2 ]
}

@test "github ssh creates the config for the keepalive with the 443 key off" {
  _ssh_github_setup false

  step_github_ssh

  [ -f "$SSH_CONFIG_FILE" ]
  grep -q '  ServerAliveInterval 30' "$SSH_CONFIG_FILE"
}

@test "github ssh is idempotent with the 443 key off" {
  _ssh_github_setup false
  _ssh_github_user_config

  step_github_ssh
  step_github_ssh

  [ "$(grep -c 'ServerAliveInterval' "$SSH_CONFIG_FILE")" -eq 1 ]
}

@test "github ssh adopts the 443 routing without a second block" {
  _ssh_github_setup false
  _ssh_github_user_config
  step_github_ssh

  printf 'github:\n  ssh_over_443: true\n' > "$WORKBENCH_CONFIG_FILE"
  step_github_ssh

  [ "$(grep -c '^Host github.com' "$SSH_CONFIG_FILE")" -eq 1 ]
  [ "$(grep -c 'ServerAliveInterval' "$SSH_CONFIG_FILE")" -eq 1 ]
  grep -q '  Hostname ssh.github.com' "$SSH_CONFIG_FILE"
}

@test "github ssh does not touch known_hosts while the 443 key is off" {
  _ssh_github_setup false
  printf 'github.com %s\n' "$SSH_GITHUB_FAKE_KEY" > "$SSH_KNOWN_HOSTS_FILE"

  step_github_ssh

  run grep 'ssh.github.com' "$SSH_KNOWN_HOSTS_FILE"
  [ "$status" -ne 0 ]
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
