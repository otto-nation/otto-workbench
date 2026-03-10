#!/bin/bash
# Shared UI helpers — sourced by all workbench scripts
#
# Sourcing patterns:
#   install.sh        . "$DOTFILES_DIR/lib/ui.sh"
#   ai/setup.sh       . "$SCRIPT_DIR/../lib/ui.sh"
#   bin/* (bash)      _SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"; . "$(dirname "$_SELF")/../lib/ui.sh"
#   bin/* (zsh)       _SELF="$(readlink "$0" 2>/dev/null || echo "$0")"; . "$(dirname "$_SELF")/../lib/ui.sh"

# shellcheck disable=SC2034  # All color variables are used by sourcing scripts
BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m'

info()    { echo -e "${BLUE}→${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
err()     { echo -e "${RED}✗${NC} $*" >&2; }

# skip [label] — print a skip line with optional label
skip() { echo -e "${DIM}⊘ ${1:-Skipped}${NC}"; }

# Prompt helpers — bash only
# read -n 1 behaves differently in zsh; these are skipped silently when sourced from a zsh script
if [[ -n "${BASH_VERSION:-}" ]]; then
  # confirm "msg" — [Y/n]; returns 0 for yes (default), 1 for no
  confirm() {
    local msg=$1
    read -r -n 1 -p "$msg [Y/n] " REPLY
    echo
    [[ ! $REPLY =~ ^[Nn]$ ]]
  }

  # confirm_n "msg" — [y/N]; returns 0 for yes, 1 for no (default)
  confirm_n() {
    local msg=$1
    read -r -n 1 -p "$msg [y/N] " REPLY
    echo
    [[ $REPLY =~ ^[Yy]$ ]]
  }

  # prompt_overwrite FILE — warns that FILE already exists and asks whether to overwrite it.
  # Offers an optional backup step before overwriting. Returns 1 (skip) if the user declines.
  prompt_overwrite() {
    local file=$1
    warn "$file already exists"
    printf "  Overwrite? [y/N] "
    read -n 1 -r REPLY
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then return 1; fi

    printf "  Create backup? [Y/n] "
    read -n 1 -r REPLY
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
      cp "$file" "${file}.backup"
      echo -e "  ${GREEN}✓${NC} Backed up to ${file}.backup"
    fi
  }

  # select_menu RESULT_VAR COUNT [--default all|skip|require] [--single]
  #
  # Displays a numbered selection prompt and writes the result back to RESULT_VAR.
  # Validates input against 1..COUNT; warns and ignores out-of-range numbers.
  # 0 always means explicit skip regardless of --default.
  #
  # --default all     Empty input selects all indices (default for multi-select menus)
  # --default skip    Empty input skips and returns "" (default for optional sub-menus)
  # --default require Empty input is rejected; caller should check return code and exit
  # --single          Accept only one number; stops after the first valid entry
  #
  # Result: space-separated 1-based indices written to RESULT_VAR, or "" for skip.
  select_menu() {
    local _result_var=$1 _count=$2
    shift 2
    local _default="skip" _single=false

    while [[ $# -gt 0 ]]; do
      case "$1" in
        --default) _default="$2"; shift 2 ;;
        --single)  _single=true;  shift   ;;
        *)                        shift   ;;
      esac
    done

    local _number_hint _default_hint
    if [[ "$_single" == true ]]; then
      _number_hint="Enter number"
    else
      _number_hint="Numbers (e.g. 1 3)"
    fi

    case "$_default" in
      all)     _default_hint=", Enter for all, or 0 to skip" ;;
      skip)    _default_hint=", or Enter to skip"            ;;
      require) _default_hint=""                              ;;
    esac

    local _raw
    read -rp "  ${_number_hint}${_default_hint}: " _raw
    echo

    if [[ -z "$_raw" ]]; then
      case "$_default" in
        all)
          local _all="" _i
          for (( _i=1; _i<=_count; _i++ )); do _all+="$_i "; done
          printf -v "$_result_var" '%s' "${_all% }"
          return 0 ;;
        skip)
          printf -v "$_result_var" '%s' ""
          skip
          return 0 ;;
        require)
          printf -v "$_result_var" '%s' ""
          return 1 ;;
      esac
    fi

    if [[ "$_raw" == "0" ]]; then
      printf -v "$_result_var" '%s' ""
      skip
      return 0
    fi

    local _selected="" _num
    for _num in $_raw; do
      if [[ "$_num" =~ ^[0-9]+$ ]] && (( _num >= 1 && _num <= _count )); then
        _selected+="$_num "
        [[ "$_single" == true ]] && break
      else
        warn "Unknown option: $_num — ignored"
      fi
    done

    printf -v "$_result_var" '%s' "${_selected% }"
  }

  # require_command NAME [MESSAGE] — returns 1 with a warning if NAME is not in PATH.
  # Caller decides whether to exit or return: require_command foo "msg" || exit 0
  require_command() {
    local name=$1 msg="${2:-$1 not found in PATH — skipping}"
    command -v "$name" >/dev/null 2>&1 && return 0
    warn "$msg"
    return 1
  }

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

    if [[ -e "$target" && ! -L "$target" ]]; then
      if [[ "$no_prompt" == true || "${SYMLINK_MODE:-}" == "no-prompt" ]]; then
        warn "$label: real file exists at $target — skipping (run install.sh to manage)"
        return
      fi
      prompt_overwrite "$target" || { skip "$label"; return; }
    fi

    ln -sfh "$source" "$target"
    echo -e "  ${GREEN}✓${NC} $label"
  }

  # symlink_dir SRC DST [GLOB] [--strip-ext]
  # Symlinks all items matching GLOB in SRC into DST, preserving filenames.
  # GLOB defaults to '*'. --strip-ext removes the file extension from the display label.
  # Inherits SYMLINK_MODE from the environment (pass-through to install_symlink).
  symlink_dir() {
    local src=$1 dst=$2
    shift 2
    local glob="*" strip_ext=false

    while [[ $# -gt 0 ]]; do
      case "$1" in
        --strip-ext) strip_ext=true; shift ;;
        *)           glob="$1";      shift ;;
      esac
    done

    local item label
    for item in "$src"/$glob; do
      [[ -e "$item" ]] || continue
      label=$(basename "$item")
      [[ "$strip_ext" == true ]] && label="${label%.*}"
      install_symlink "$item" "$dst/$(basename "$item")" "$label"
    done
  }
fi
