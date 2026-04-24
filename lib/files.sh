#!/usr/bin/env bash
# File operation helpers — symlinks, copies, and config patching.
# Bash-only (uses local, arrays, and prompt helpers).
#
# Functions: install_symlink, install_file, copy_dir, symlink_dir, apply_config_patch

[[ -n "${_LIB_FILES_SH:-}" ]] && return
_LIB_FILES_SH=1

# Ensure dependencies are available
_files_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=output.sh
. "$_files_lib_dir/output.sh"
# shellcheck source=prompts.sh
. "$_files_lib_dir/prompts.sh"
unset _files_lib_dir

# install_symlink SOURCE TARGET [LABEL] [--no-prompt]
# Creates or updates a symlink at TARGET pointing to SOURCE.
# Existing symlinks are silently replaced. Real files at TARGET:
#   default (or SYMLINK_MODE unset): prompt before overwriting
#   --no-prompt or SYMLINK_MODE=no-prompt: warn and skip (for non-interactive sync)
# LABEL defaults to basename of SOURCE.
# -h prevents BSD ln from dereferencing an existing directory symlink on re-runs.
install_symlink() {
  local source=$1 target=$2
  shift 2
  local label="" no_prompt=false

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --no-prompt) no_prompt=true; shift ;;
      *)           label="$1";     shift ;;
    esac
  done

  [[ -z "$label" ]] && label=$(basename "$source")

  if [[ -L "$target" ]] && [[ "$(readlink "$target")" == "$source" ]]; then
    echo -e "  ${DIM}✓ $label${NC}"
    return
  fi

  if [[ -e "$target" && ! -L "$target" ]]; then
    if [[ "$no_prompt" == true || "${SYMLINK_MODE:-}" == "no-prompt" ]]; then
      warn "$label: real file exists at $target — skipping (run install.sh to manage)"
      return
    fi
    prompt_overwrite "$target" || { skip "$label"; return; }
  fi

  # -sfh (BSD/macOS) and -sfn (GNU/Linux) both prevent following an existing symlink
  # at the destination — without this, ln -sf on a dir symlink nests inside it.
  if ln --version &>/dev/null 2>&1; then
    ln -sfn "$source" "$target"   # GNU ln
  else
    ln -sfh "$source" "$target"   # BSD ln (macOS)
  fi
  echo -e "  ${GREEN}✓${NC} $label"
}

# install_file SOURCE TARGET [LABEL]
# Copies SOURCE to TARGET if content differs. Removes stale symlinks at TARGET.
# Idempotent — no-op if file is already up to date.
install_file() {
  local source=$1 target=$2
  shift 2
  local label="${1:-$(basename "$source")}"

  if [[ -L "$target" ]]; then
    rm "$target"
  fi

  if [[ -f "$target" ]] && diff -q "$source" "$target" &>/dev/null; then
    echo -e "  ${DIM}✓ $label${NC}"
    return
  fi

  cp "$source" "$target"
  echo -e "  ${GREEN}✓${NC} $label"
}

# copy_dir SRC DST [GLOB] [--strip-ext] [--prune]
# Copies all files matching GLOB in SRC into DST, preserving filenames.
# GLOB defaults to '*'. --strip-ext removes the file extension from the display label.
# --prune removes stale files (or symlinks) in DST whose source counterpart is gone.
copy_dir() {
  local src="${1%/}" dst="$2"
  shift 2
  local glob="*" strip_ext=false prune=false

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --strip-ext) strip_ext=true; shift ;;
      --prune)     prune=true;     shift ;;
      *)           glob="$1";      shift ;;
    esac
  done

  if [[ "$prune" == true ]]; then
    local item
    for item in "$dst"/$glob; do
      [[ -e "$item" || -L "$item" ]] || continue
      [[ ! -e "$src/$(basename "$item")" ]] || continue
      rm "$item"
      echo -e "  ${DIM}⊘ pruned $(basename "$item")${NC}"
    done
  fi

  local item label
  for item in "$src"/$glob; do
    [[ -f "$item" ]] || continue
    label=$(basename "$item")
    [[ "$strip_ext" == true ]] && label="${label%.*}"
    install_file "$item" "$dst/$(basename "$item")" "$label"
  done
}

# symlink_dir SRC DST [GLOB] [--strip-ext] [--prune] [--replace-copies]
# Symlinks all items matching GLOB in SRC into DST, preserving filenames.
# GLOB defaults to '*'. --strip-ext removes the file extension from the display label.
# --prune removes stale symlinks in DST that point into SRC but whose source is gone.
# --replace-copies removes regular files in DST that have a source counterpart,
#   allowing install_symlink to replace them. Used when migrating from copy_dir.
# Inherits SYMLINK_MODE from the environment (pass-through to install_symlink).
symlink_dir() {
  local src="${1%/}" dst="$2"  # strip trailing slash so item paths never contain //
  shift 2
  local glob="*" strip_ext=false prune=false replace_copies=false

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --strip-ext)       strip_ext=true;      shift ;;
      --prune)           prune=true;           shift ;;
      --replace-copies)  replace_copies=true;  shift ;;
      *)                 glob="$1";            shift ;;
    esac
  done

  # Replace regular files with source counterparts so install_symlink can create symlinks.
  # install_symlink skips (no-prompt) or prompts (interactive) when a real file exists.
  if [[ "$replace_copies" == true ]]; then
    local item
    for item in "$dst"/$glob; do
      [[ -f "$item" && ! -L "$item" ]] || continue
      [[ -e "$src/$(basename "$item")" ]] || continue
      rm "$item"
    done
  fi

  if [[ "$prune" == true ]]; then
    local item target
    for item in "$dst"/$glob; do
      [[ -L "$item" ]] || continue
      target=$(readlink "$item")
      [[ "$target" == "$src"/* ]] || continue
      if [[ ! -e "$target" ]]; then
        rm "$item"
        echo -e "  ${DIM}⊘ pruned $(basename "$item")${NC}"
      fi
    done
  fi

  local item label
  for item in "$src"/$glob; do
    [[ -e "$item" ]] || continue
    label=$(basename "$item")
    [[ "$strip_ext" == true ]] && label="${label%.*}"
    install_symlink "$item" "$dst/$(basename "$item")" "$label"
  done
}

# sync_component_bin COMPONENT_DIR — symlinks extensionless scripts from
# COMPONENT_DIR/bin/ into LOCAL_BIN_DIR. No-op if bin/ subdirectory is absent.
sync_component_bin() {
  local component_bin="$1/bin"
  [[ -d "$component_bin" ]] || return 0
  mkdir -p "$LOCAL_BIN_DIR"
  shopt -s extglob
  symlink_dir "$component_bin" "$LOCAL_BIN_DIR" "!(*.*)" --prune
  shopt -u extglob
}

# resolve_layers BASE_DIR USER_DIR GLOB RESULT_NAMEREF
# Merges two directory layers into an associative array: basename -> source_path.
# User dir wins for same-named files. A .disabled sentinel in user dir suppresses both.
# RESULT_NAMEREF must be a declared associative array in the caller.
resolve_layers() {
  local base_dir="${1%/}" user_dir="${2%/}" glob="$3"
  local -n __result=$4

  # Base layer — all matching items
  local item name
  for item in "$base_dir"/$glob; do
    [[ -e "$item" ]] || continue
    item="${item%/}"  # strip trailing slash from directory globs
    name=$(basename "$item")
    __result["$name"]="$item"
  done

  # User layer — overrides and additions
  if [[ -d "$user_dir" ]]; then
    for item in "$user_dir"/$glob; do
      [[ -e "$item" ]] || continue
      item="${item%/}"
      name=$(basename "$item")
      __result["$name"]="$item"
    done

    # Disable layer — .disabled sentinels suppress both layers
    for item in "$user_dir"/*.disabled; do
      [[ -e "$item" ]] || continue
      name=$(basename "$item" .disabled)
      unset "__result[$name]"
      # Try common extensions
      unset "__result[${name}.md]"
    done
  fi
}

# is_disabled USER_DIR NAME — returns 0 if a .disabled sentinel exists.
is_disabled() {
  [[ -f "${1%/}/${2}.disabled" ]]
}

# apply_config_patch FILE OLD NEW
# Replaces OLD with NEW in FILE if OLD is present. Idempotent — no-op if already patched
# or if FILE does not exist. Assumes OLD and NEW do not contain the | character.
# Called by component migrations.sh files via run_migrations.
apply_config_patch() {
  local file="$1" old="$2" new="$3"
  [[ -f "$file" ]] || return 0
  grep -qF "$old" "$file" || return 0
  sed_i "s|$old|$new|g" "$file"
  success "Patched $(basename "$file"): '$old' → '$new'"
}
