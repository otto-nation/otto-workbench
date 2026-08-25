#!/usr/bin/env bash
# Shared setup helpers for workbench bats tests.

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

# _assert_not_real_repo — fails hard if PWD is inside the real workbench repo.
_assert_not_real_repo() {
  local toplevel
  toplevel="$(git rev-parse --show-toplevel 2>/dev/null)" || return 0
  if [[ "$toplevel" == "$REPO_ROOT" ]]; then
    echo "FATAL: test is operating inside the real repo ($PWD)"
    echo "  REPO_ROOT=$REPO_ROOT"
    echo "  git toplevel=$toplevel"
    return 1
  fi
}

# common_setup — call first in every test's setup(), and in any setup_file()
# that runs git.
# Prevents tests from accidentally targeting the real repo via inherited env,
# and detaches every git command a test runs from the machine's own config.
common_setup() {
  # Clear git env vars inherited from hooks (pre-push sets GIT_DIR which
  # causes git commands in tests to target the real repo instead of temp repos)
  unset GIT_DIR GIT_WORK_TREE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES 2>/dev/null || true

  # Give git a config of its own, so a temp repo inherits nothing the developer
  # set. A workbench machine turns on core.fsmonitor, core.untrackedCache, and a
  # global core.hooksPath, none of which a test asks for and two of which it pays
  # for: fsmonitor leaves a `git fsmonitor--daemon` per temp repo holding the bats
  # runner's stdout, so a case ends not when its commands do but when the daemon
  # it orphaned exits, and the global pre-commit runs gitleaks over every staged
  # file of a fixture. A clean CI runner has none of this, so the price is
  # invisible in pass/fail and shows up only as a suite that appears to hang.
  #
  # Emptying the two config files beats naming each setting through
  # GIT_CONFIG_COUNT: no list has to be kept current, so a key added to
  # gitconfig.shared tomorrow is excluded already, and a repo's own config still
  # wins — a test whose subject is a hook firing plants it in .git/hooks and it
  # runs, which a `-c core.hooksPath` would silently override. Both are exported
  # because a tool under test reads them in a subprocess.
  #
  # The global one is a writable path rather than /dev/null so `git config
  # --global`, which sync_git calls, still succeeds; git cannot lock /dev/null.
  # Nothing writes the system config, so /dev/null is enough there. A test that
  # wants a global config with content in it re-exports GIT_CONFIG_GLOBAL after
  # this call, pointing at a file it wrote — see lint_sweep.bats.
  export GIT_CONFIG_GLOBAL="${BATS_TEST_TMPDIR:-$BATS_FILE_TMPDIR}/gitconfig-global"
  export GIT_CONFIG_SYSTEM=/dev/null
}

# common_teardown — call last in every test's teardown().
# Isolation is enforced preventively: common_setup clears git env vars,
# _assert_not_real_repo guards git helpers, and tests use mktemp dirs.
common_teardown() {
  :
}

# sandbox_state_dir — points the state root at $TMPDIR/state.
# Call after TMPDIR is set. Tools that write a trail through the state root
# (dream-scan, promote-scan, retro-scan) need this or they append to the real one.
sandbox_state_dir() {
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
}

# source_lib — loads all lib/ai/*.sh files into the current test context.
source_lib() {
  local f
  for f in "$REPO_ROOT/lib/ai/"*.sh; do
    # shellcheck disable=SC1090
    source "$f"
  done
}

# make_ai_config DIR COMMAND — writes a taskfile.env with AI_COMMAND=COMMAND.
make_ai_config() {
  local dir="$1"
  local command="$2"
  mkdir -p "$dir/.config/task"
  echo "AI_COMMAND=$command" > "$dir/.config/task/taskfile.env"
}

# make_gh_token_config DIR TOKEN — writes a taskfile.env with GH_TOKEN=TOKEN.
make_gh_token_config() {
  local dir="$1"
  local token="$2"
  mkdir -p "$dir/.config/task"
  echo "GH_TOKEN=$token" > "$dir/.config/task/taskfile.env"
}

# make_git_repo_with_org DIR ORG REPO — creates a git repo with origin pointing to github.com:ORG/REPO.
make_git_repo_with_org() {
  local dir="$1"
  local org="$2"
  local repo="$3"
  mkdir -p "$dir"
  common_setup
  GIT_CEILING_DIRECTORIES="$(dirname "$dir")" git -C "$dir" init --quiet
  git -C "$dir" remote add origin "git@github.com:${org}/${repo}.git"
}

# make_container_seed DIR — commits whatever DIR already holds as one commit on
# `main`, and adds a `feat` branch. The repo a bare-repo container is cloned from.
#
# --initial-branch is pinned rather than inherited from init.defaultBranch,
# because which branch a container calls default is the thing under test: a
# runner whose git still defaults to master would fail here in setup rather than
# in an assertion.
make_container_seed() {
  local dir="$1"
  mkdir -p "$dir"
  git -C "$dir" init -q --initial-branch=main
  git -C "$dir" config user.email test@example.com
  git -C "$dir" config user.name Test
  git -C "$dir" add -A
  git -C "$dir" commit -qm init
  git -C "$dir" branch feat
}

# make_empty_container CONTAINER SEED — a bare clone of SEED at CONTAINER/.git
# and nothing else: a container whose default branch has no checkout, which is
# the case every writer of a project artifact has to refuse rather than fall
# through on.
make_empty_container() {
  local container="$1" seed="$2"
  mkdir -p "$container"
  git clone -q --bare "$seed" "$container/.git"
}

# make_worktree_container CONTAINER SEED — the layout wt-init produces: the bare
# clone of SEED at CONTAINER/.git with the `main` worktree checked out beside it.
make_worktree_container() {
  local container="$1" seed="$2"
  make_empty_container "$container" "$seed"
  git -C "$container" worktree add -q "$container/main" main
}

# make_fake_binary DIR NAME — creates an executable stub in DIR/NAME.
make_fake_binary() {
  local dir="$1"
  local name="$2"
  mkdir -p "$dir"
  printf '#!/bin/bash\necho "fake output"\n' > "$dir/$name"
  chmod +x "$dir/$name"
}

# make_fake_task_dir REPO_ROOT — creates $TMPDIR/fake-task-config with a
# `lib` symlink into REPO_ROOT, echoing the fake dir's path. Simulates the
# ~/.config/task install layout: Taskfile.yml and lib/ are symlinks, and
# nothing else is reachable from the fake dir.
make_fake_task_dir() {
  local repo_root="$1"
  local fake_task_dir="$BATS_TEST_TMPDIR/fake-task-config"
  mkdir -p "$fake_task_dir"
  ln -s "$repo_root/lib" "$fake_task_dir/lib"
  printf '%s' "$fake_task_dir"
}

# make_fake_gh EXIT_CODE OUTPUT — stub gh that records its arguments in
# $TMPDIR/gh-args.txt (path exposed via GH_ARGS_FILE) and prints OUTPUT.
# Failures print on stderr, matching how gh reports errors. Callers that only
# need gh to succeed quietly (e.g. as an entry-gate check) can pass `0 ""`.
make_fake_gh() {
  local exit_code="$1"
  local output="$2"
  local stream=1
  [[ "$exit_code" -eq 0 ]] || stream=2
  mkdir -p "$TMPDIR/bin"
  GH_ARGS_FILE="$TMPDIR/gh-args.txt"
  cat > "$TMPDIR/bin/gh" << SCRIPT
#!/bin/bash
printf '%s\n' "\$@" > "$GH_ARGS_FILE"
printf '%s\n' "$output" >&$stream
exit $exit_code
SCRIPT
  chmod +x "$TMPDIR/bin/gh"
  PATH="$TMPDIR/bin:$PATH"
}

# _make_repo_no_default_branch DIR [INITIAL_BRANCH] [EXTRA_BRANCH] — bare remote +
# clone with one commit, the way an unfetched clone or a `wt-init`-converted
# repo ends up: no refs/remotes/origin/HEAD symref, because the clone happened
# before the remote had any commit for HEAD to point at. When EXTRA_BRANCH is
# given, checks it out with one more commit on top — for callers that also
# need to be off the default branch (e.g. load_pr_context's protected-branch guard).
_make_repo_no_default_branch() {
  local dir="$1"
  local initial_branch="${2:-main}"
  local extra_branch="${3:-}"
  git init --bare "$dir/remote.git" --quiet --initial-branch="$initial_branch"
  git clone "$dir/remote.git" "$dir/repo" --quiet 2>/dev/null
  git -C "$dir/repo" config user.email "test@example.com"
  git -C "$dir/repo" config user.name "Test"
  echo "init" > "$dir/repo/README.md"
  git -C "$dir/repo" add .
  git -C "$dir/repo" commit -m "initial" --quiet
  git -C "$dir/repo" push --quiet

  [[ -z "$extra_branch" ]] && return 0
  git -C "$dir/repo" checkout -b "$extra_branch" --quiet
  echo "feature" > "$dir/repo/feature.txt"
  git -C "$dir/repo" add .
  git -C "$dir/repo" commit -m "feat: add feature" --quiet
}

# make_git_remote REMOTE_DIR LOCAL_DIR BRANCH — sets up a bare remote, clones it,
# makes an initial commit on main, then creates BRANCH with one commit.
make_git_remote() {
  local remote_dir="$1"
  local local_dir="$2"
  local branch="${3:-feature/test}"

  common_setup
  GIT_CEILING_DIRECTORIES="$(dirname "$local_dir")"
  export GIT_CEILING_DIRECTORIES

  git init --bare "$remote_dir" --quiet --initial-branch=main
  git clone "$remote_dir" "$local_dir" --quiet 2>/dev/null

  [[ -d "$local_dir/.git" ]] || {
    echo "FATAL: git clone failed — $local_dir/.git does not exist"
    return 1
  }

  cd "$local_dir" || return 1
  _assert_not_real_repo || return 1

  git config user.email "test@example.com"
  git config user.name "Test"

  echo "init" > README.md
  git add .
  git commit -m "initial" --quiet
  git push --quiet

  git checkout -b "$branch" --quiet
  echo "feature" > feature.txt
  git add .
  git commit -m "feat: add feature" --quiet
}

# clone_from_shared_remote REMOTE_DIR LOCAL_DIR [BRANCH] — fast local clone from
# a bare remote created by make_git_remote in setup_file. Use this in per-test
# setup() to avoid repeating the expensive init/commit/push cycle.
clone_from_shared_remote() {
  local remote_dir="$1"
  local local_dir="$2"
  local branch="${3:-feature/test}"

  common_setup
  GIT_CEILING_DIRECTORIES="$(dirname "$local_dir")"
  export GIT_CEILING_DIRECTORIES

  cd / || return 1
  git clone "$remote_dir" "$local_dir" --quiet 2>/dev/null

  [[ -d "$local_dir/.git" ]] || {
    echo "FATAL: git clone failed — $local_dir/.git does not exist"
    return 1
  }

  cd "$local_dir" || return 1
  _assert_not_real_repo || return 1

  git config user.email "test@example.com"
  git config user.name "Test"
  git checkout "$branch" --quiet 2>/dev/null
}
