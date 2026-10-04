#!/usr/bin/env bats
# Tests that lib/projects.sh and ai/lib/config/workbench_projects.py agree on the registry.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  load 'projects_helper'
  # Fully resolved: on macOS mktemp hands back a /var/folders path that git
  # reports as /private/var/folders, and half these assertions compare the two.
  TMPDIR="$(cd "$BATS_TEST_TMPDIR" && pwd -P)"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  export WORKBENCH_CACHE_DIR="$TMPDIR/cache"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"

  # Everything a test builds lives in a temp directory, which is precisely what
  # the default exclusion list refuses. The sandboxed state root still keeps the
  # writes out of the real registry.
  # shellcheck disable=SC2034  # read by lib/projects.sh
  PROJECTS_EXCLUDED_PREFIXES=("$WORKBENCH_STATE_DIR" "$WORKBENCH_CACHE_DIR")

  # shellcheck source=../lib/ui.sh
  . "$REPO_ROOT/lib/ui.sh"
}

teardown() {
  common_teardown
}

# ─── Cross-language agreement ────────────────────────────────────────────────

@test "the repo id bash records names the container Python resolves" {
  # lib/git_layout.py owns container resolution for Python and answers None for
  # an ordinary clone; the bash id is total and one level deeper. The two agree
  # about where a bare-repo container is, which is the half they share.
  make_bare_worktree_layout "$TMPDIR/container"

  run git_shared_dir "$TMPDIR/container/main"
  [ "$status" -eq 0 ]
  local shared="$output"

  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/lib')
import git_layout
print(git_layout.container_dir('$TMPDIR/container/main'))
"
  [ "$status" -eq 0 ]
  [ "$output" = "$(dirname "$shared")" ]
}

@test "bash and Python name the same shared git dir" {
  # shared_dir is the total Python mirror of git_shared_dir: ordinary clones
  # answer with their .git, bare-repo worktrees with the container's .git.
  make_bare_worktree_layout "$TMPDIR/container"
  make_repo "$TMPDIR/alpha"

  run git_shared_dir "$TMPDIR/container/main"
  [ "$status" -eq 0 ]
  local bare_shared="$output"

  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/lib')
import git_layout
print(git_layout.shared_dir('$TMPDIR/container/main'), end='')
"
  [ "$status" -eq 0 ]
  [ "$output" = "$bare_shared" ]

  run git_shared_dir "$TMPDIR/container/feature"
  [ "$status" -eq 0 ]
  [ "$output" = "$bare_shared" ]

  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/lib')
import git_layout
print(git_layout.shared_dir('$TMPDIR/container/feature'), end='')
"
  [ "$status" -eq 0 ]
  [ "$output" = "$bare_shared" ]

  run git_shared_dir "$TMPDIR/alpha"
  [ "$status" -eq 0 ]
  local clone_shared="$output"
  [ "$clone_shared" = "$TMPDIR/alpha/.git" ]

  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/lib')
import git_layout
print(git_layout.shared_dir('$TMPDIR/alpha'), end='')
"
  [ "$status" -eq 0 ]
  [ "$output" = "$clone_shared" ]

  mkdir -p "$TMPDIR/plain"
  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/lib')
import git_layout
print(git_layout.shared_dir('$TMPDIR/plain'))
"
  [ "$status" -eq 0 ]
  [ "$output" = "None" ]
}

@test "bash and Python name the same registry file" {
  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
from config import workbench_projects
print(workbench_projects.registry_path())
"
  [ "$status" -eq 0 ]
  [ "$output" = "$PROJECTS_REGISTRY_FILE" ]
}

@test "a repo Python registered is a repo bash reads" {
  make_repo "$TMPDIR/alpha"
  run python3 -c "
import os, sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
from config import workbench_projects
workbench_projects.TEMP_ROOTS = ()
os.environ.pop('TMPDIR', None)
assert workbench_projects.register('$TMPDIR/alpha')
"
  [ "$status" -eq 0 ]

  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "a repo bash registered is a repo Python reads" {
  make_repo "$TMPDIR/alpha"
  project_register "$TMPDIR/alpha"

  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
from config import workbench_projects
print(*workbench_projects.registered())
"
  [ "$status" -eq 0 ]
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "bash and Python exclude the same temporary roots" {
  # Two languages spelling one membership rule — the cross-validation the SSOT
  # convention asks for when a default has to exist in both.
  unset PROJECTS_EXCLUDED_PREFIXES
  # shellcheck source=../lib/projects.sh
  . "$REPO_ROOT/lib/projects.sh"

  local prefix
  local -a fixed=()
  for prefix in "${PROJECTS_EXCLUDED_PREFIXES[@]}"; do
    # The rest of the list is derived from the environment, not fixed.
    if [[ "$prefix" == "${TMPDIR%/}" || "$prefix" == "$WORKBENCH_STATE_DIR" ]]; then
      continue
    fi
    if [[ "$prefix" == "$WORKBENCH_CACHE_DIR" || "$prefix" == "$WORKBENCH_DATA_DIR" ]]; then
      continue
    fi
    fixed+=("$prefix")
  done

  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
from config import workbench_projects
print('\n'.join(sorted(workbench_projects.TEMP_ROOTS)))
"
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf '%s\n' "${fixed[@]}" | sort)" ]
}

@test "both halves refuse a bare repo's container" {
  make_bare_container "$TMPDIR/container"
  run project_register "$TMPDIR/container"
  [ "$status" -eq 1 ]

  run python3 -c "
import os, sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
from config import workbench_projects
workbench_projects.TEMP_ROOTS = ()
os.environ.pop('TMPDIR', None)
print(workbench_projects.register('$TMPDIR/container'))
"
  [ "$output" = "False" ]
}

@test "Python reads the path from a line bash gave a repo id" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\t%s\n' "$TMPDIR/alpha" "$TMPDIR/alpha/.git" > "$PROJECTS_REGISTRY_FILE"

  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
from config import workbench_projects
print(*workbench_projects.registered())
"
  [ "$status" -eq 0 ]
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "Python does not append a repo bash recorded with a repo id" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\t%s\n' "$TMPDIR/alpha" "$TMPDIR/alpha/.git" > "$PROJECTS_REGISTRY_FILE"

  run python3 -c "
import os, sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
from config import workbench_projects
workbench_projects.TEMP_ROOTS = ()
os.environ.pop('TMPDIR', None)
assert workbench_projects.register('$TMPDIR/alpha')
"
  [ "$status" -eq 0 ]
  run grep -c . "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "1" ]
}
