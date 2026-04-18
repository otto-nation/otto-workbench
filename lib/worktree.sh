#!/usr/bin/env bash
# Git worktree lifecycle helpers — create, list, status, remove worktrees.
#
# Worktrees live at <repo-root>/.worktrees/<name>/ with auto-named branches
# following the project's branch naming convention (username/type/description).
#
# No AI dependency — works without taskfile.env configuration.
#
# Sourced by Taskfile wt:* tasks:
#   . "{{.TASKFILE_DIR}}/lib/worktree.sh"

[[ -n "${_LIB_WORKTREE_SH:-}" ]] && return
_LIB_WORKTREE_SH=1

WORKTREE_DIR_NAME=".worktrees"

# ─── Internal helpers ─────────────────────────────────────────────────────────

# _wt_source_output — loads output helpers (info, success, warn, err) if not
# already available. Resolves relative to this file's location.
_wt_source_output() {
  [[ -n "${_LIB_OUTPUT_SH:-}" ]] && return
  local lib_dir
  lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # shellcheck source=output.sh
  . "$lib_dir/output.sh"
}

_wt_source_output

# ─── Core functions ───────────────────────────────────────────────────────────

# wt_repo_root — resolves the main working tree root, even when called from
# inside a worktree. Sets WT_REPO_ROOT.
wt_repo_root() {
  local git_common_dir
  git_common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || {
    err "Not inside a git repository"
    return 1
  }
  # git-common-dir points to the .git directory of the main working tree.
  # Strip the trailing /.git and resolve symlinks for consistent paths.
  WT_REPO_ROOT="$(cd "${git_common_dir%/.git}" && pwd -P)"
}

# wt_detect_username — auto-detects the git username for branch naming.
# Normalizes to lowercase, replaces spaces with hyphens. Sets WT_USERNAME.
wt_detect_username() {
  local raw
  raw="$(git config user.name 2>/dev/null)" || raw="$(id -un)"
  # Lowercase, spaces to hyphens, take first token (first name)
  raw="${raw,,}"
  raw="${raw// /-}"
  # Use first name only (before first hyphen) to match branch convention
  WT_USERNAME="${raw%%-*}"
}

# wt_build_branch NAME — constructs a branch name following the convention
# username/type/description. If NAME already contains a /, it's treated as
# type/description and only the username is prepended.
# Sets WT_BRANCH.
wt_build_branch() {
  local name="$1"
  wt_detect_username

  if [[ "$name" == */* ]]; then
    # Name already has structure (e.g. feat/add-search or PROJ-123/description)
    WT_BRANCH="${WT_USERNAME}/${name}"
  else
    # Plain name — add feat/ prefix
    WT_BRANCH="${WT_USERNAME}/feat/${name}"
  fi
}

# wt_ensure_gitignore — ensures .worktrees is excluded from git tracking.
# Uses the global gitignore (~/.config/git/ignore). Creates the file and sets
# core.excludesFile if needed. Idempotent.
wt_ensure_gitignore() {
  local ignore_file="${HOME}/.config/git/ignore"
  local ignore_dir
  ignore_dir="$(dirname "$ignore_file")"

  # Ensure core.excludesFile is set
  local current_excludes
  current_excludes="$(git config --global core.excludesFile 2>/dev/null || true)"
  if [[ -z "$current_excludes" ]]; then
    git config --global core.excludesFile "$ignore_file"
  fi

  # Create file if missing
  mkdir -p "$ignore_dir"
  [[ -f "$ignore_file" ]] || touch "$ignore_file"

  # Add .worktrees if not already present
  if ! grep -qxF ".worktrees" "$ignore_file" 2>/dev/null; then
    echo ".worktrees" >> "$ignore_file"
  fi
}

# wt_create NAME [BASE] — creates a worktree at .worktrees/<name>/ with a new
# branch. BASE defaults to origin/main. Fetches first to ensure the base is current.
wt_create() {
  local name="$1"
  local base="${2:-origin/main}"

  wt_repo_root || return 1
  wt_build_branch "$name"
  wt_ensure_gitignore

  local wt_dir="${WT_REPO_ROOT}/${WORKTREE_DIR_NAME}/${name}"

  if [[ -d "$wt_dir" ]]; then
    err "Worktree already exists: ${WORKTREE_DIR_NAME}/${name}"
    return 1
  fi

  # Check if branch already exists
  if git show-ref --verify --quiet "refs/heads/${WT_BRANCH}" 2>/dev/null; then
    err "Branch already exists: ${WT_BRANCH}"
    info "Use a different name or remove the existing branch first"
    return 1
  fi

  info "Fetching origin..."
  git fetch origin --quiet 2>/dev/null || true

  info "Creating worktree: ${WORKTREE_DIR_NAME}/${name}"
  info "Branch: ${WT_BRANCH} (from ${base})"

  git worktree add -b "$WT_BRANCH" "$wt_dir" "$base" --quiet || {
    err "Failed to create worktree"
    return 1
  }

  success "Worktree created"
  echo ""
  info "Path: ${wt_dir}"
  info "Enter: cd ${wt_dir}"
}

# wt_list — lists all worktrees under .worktrees/ with branch and status info.
wt_list() {
  wt_repo_root || return 1

  local wt_base="${WT_REPO_ROOT}/${WORKTREE_DIR_NAME}"

  if [[ ! -d "$wt_base" ]]; then
    info "No worktrees found"
    return 0
  fi

  local found=false
  local wt_path wt_branch dirty ahead behind status_info

  while IFS= read -r line; do
    # Parse porcelain output: "worktree <path>" lines followed by "branch <ref>"
    if [[ "$line" == "worktree "* ]]; then
      wt_path="${line#worktree }"
      # Only show worktrees under .worktrees/
      if [[ "$wt_path" != "${wt_base}/"* ]]; then
        wt_path=""
        continue
      fi
    elif [[ "$line" == "branch "* && -n "$wt_path" ]]; then
      wt_branch="${line#branch refs/heads/}"
      found=true

      # Check for uncommitted changes
      dirty=""
      if ! git -C "$wt_path" diff --quiet 2>/dev/null || \
         ! git -C "$wt_path" diff --cached --quiet 2>/dev/null; then
        dirty=" ${YELLOW}[modified]${NC}"
      fi

      # Check ahead/behind
      status_info=""
      ahead="$(git -C "$wt_path" rev-list --count "@{upstream}..HEAD" 2>/dev/null || echo 0)"
      behind="$(git -C "$wt_path" rev-list --count "HEAD..@{upstream}" 2>/dev/null || echo 0)"
      [[ "$ahead" -gt 0 ]] && status_info+=" ${GREEN}+${ahead}${NC}"
      [[ "$behind" -gt 0 ]] && status_info+=" ${RED}-${behind}${NC}"

      local short_name="${wt_path#"${wt_base}/"}"
      echo -e "  ${BOLD}${short_name}${NC}  ${DIM}${wt_branch}${NC}${dirty}${status_info}"

      wt_path=""
    elif [[ -z "$line" ]]; then
      wt_path=""
    fi
  done < <(git worktree list --porcelain)

  if [[ "$found" != true ]]; then
    info "No worktrees found"
  fi
}

# wt_status [NAME] — shows detailed status of a named worktree, or the current
# worktree if inside one. With no argument from the main working tree, shows
# status of all worktrees.
wt_status() {
  local name="${1:-}"
  wt_repo_root || return 1

  local wt_base="${WT_REPO_ROOT}/${WORKTREE_DIR_NAME}"
  local target_path

  if [[ -n "$name" ]]; then
    # Explicit name
    name="${name// /}"
    target_path="${wt_base}/${name}"
    if [[ ! -d "$target_path" ]]; then
      err "Worktree not found: ${name}"
      return 1
    fi
  else
    # Detect if we're inside a worktree
    local git_dir git_common
    git_dir="$(git rev-parse --git-dir 2>/dev/null)"
    git_common="$(git rev-parse --git-common-dir 2>/dev/null)"
    if [[ "$git_dir" != "$git_common" ]]; then
      # Inside a worktree — use current directory
      target_path="$(git rev-parse --show-toplevel)"
    else
      # Not in a worktree — list all
      info "Not inside a worktree. Showing all:"
      echo ""
      wt_list
      return 0
    fi
  fi

  local branch last_commit dirty_count staged_count ahead behind

  branch="$(git -C "$target_path" branch --show-current 2>/dev/null)"
  last_commit="$(git -C "$target_path" log -1 --format='%h %s' 2>/dev/null || echo 'no commits')"
  dirty_count="$(git -C "$target_path" diff --name-only 2>/dev/null | wc -l | tr -d ' ')"
  staged_count="$(git -C "$target_path" diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')"
  ahead="$(git -C "$target_path" rev-list --count "@{upstream}..HEAD" 2>/dev/null || echo 0)"
  behind="$(git -C "$target_path" rev-list --count "HEAD..@{upstream}" 2>/dev/null || echo 0)"

  local display_name="${target_path#"${wt_base}/"}"
  title "${display_name}"
  echo -e "  ${CYAN}Branch${NC}     ${branch}"
  echo -e "  ${CYAN}Path${NC}       ${target_path}"
  echo -e "  ${CYAN}Last commit${NC} ${last_commit}"
  echo -e "  ${CYAN}Modified${NC}   ${dirty_count} file(s)"
  echo -e "  ${CYAN}Staged${NC}     ${staged_count} file(s)"
  echo -e "  ${CYAN}Ahead${NC}      ${ahead} commit(s)"
  echo -e "  ${CYAN}Behind${NC}     ${behind} commit(s)"
}

# wt_remove NAME [--force] — removes a worktree and optionally deletes its branch.
# Aborts on uncommitted changes unless --force is passed.
wt_remove() {
  local name="$1"
  local force_flag="${2:-}"

  wt_repo_root || return 1

  local wt_dir="${WT_REPO_ROOT}/${WORKTREE_DIR_NAME}/${name}"

  if [[ ! -d "$wt_dir" ]]; then
    err "Worktree not found: ${name}"
    return 1
  fi

  # Check for uncommitted changes
  if [[ "$force_flag" != "--force" ]]; then
    if ! git -C "$wt_dir" diff --quiet 2>/dev/null || \
       ! git -C "$wt_dir" diff --cached --quiet 2>/dev/null; then
      err "Worktree has uncommitted changes: ${name}"
      info "Use --force to remove anyway"
      return 1
    fi
  fi

  # Capture branch name before removal
  local branch
  branch="$(git -C "$wt_dir" branch --show-current 2>/dev/null || true)"

  info "Removing worktree: ${name}"
  if [[ "$force_flag" == "--force" ]]; then
    git worktree remove "$wt_dir" --force
  else
    git worktree remove "$wt_dir"
  fi
  success "Worktree removed"

  # Prune stale worktree references
  git worktree prune 2>/dev/null || true

  # Offer to delete the branch
  if [[ -n "$branch" ]] && git show-ref --verify --quiet "refs/heads/${branch}" 2>/dev/null; then
    info "Branch still exists: ${branch}"
    if [[ -t 0 ]]; then
      printf "  Delete branch? [y/N] "
      read -r answer
      if [[ "$answer" =~ ^[yY]$ ]]; then
        git branch -d "$branch" 2>/dev/null || {
          warn "Branch not fully merged. Use 'git branch -D ${branch}' to force delete"
        }
      fi
    else
      info "Run 'git branch -d ${branch}' to delete it"
    fi
  fi

  # Remove .worktrees dir if empty
  if [[ -d "${WT_REPO_ROOT}/${WORKTREE_DIR_NAME}" ]]; then
    rmdir "${WT_REPO_ROOT}/${WORKTREE_DIR_NAME}" 2>/dev/null || true
  fi
}

# wt_path NAME — prints the absolute path to a named worktree.
wt_path() {
  local name="$1"
  wt_repo_root || return 1

  local wt_dir="${WT_REPO_ROOT}/${WORKTREE_DIR_NAME}/${name}"

  if [[ ! -d "$wt_dir" ]]; then
    err "Worktree not found: ${name}" >&2
    return 1
  fi

  echo "$wt_dir"
}
