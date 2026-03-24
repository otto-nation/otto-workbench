#!/bin/bash
# Shared UI helpers and path constants — sourced by all workbench scripts
#
# Sourcing patterns:
#   install.sh        . "$DOTFILES_DIR/lib/ui.sh"
#   ai/setup.sh       . "$SCRIPT_DIR/../lib/ui.sh"
#   bin/* (bash)      _SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"; . "$(dirname "$_SELF")/../lib/ui.sh"
#   bin/* (zsh)       _SELF="$(readlink "$0" 2>/dev/null || echo "$0")"; . "$(dirname "$_SELF")/../lib/ui.sh"

# Source path and filename constants — resolved relative to this file in bash
if [[ -n "${BASH_VERSION:-}" ]]; then
  # shellcheck source=./constants.sh
  . "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/constants.sh"
fi

# Color semantics: RED=error  YELLOW=warn  GREEN=success  BLUE=info/arrow
#                  CYAN=section label  DIM=metadata/detail  BOLD=emphasis
# Respect NO_COLOR (https://no-color.org) and non-terminal stdout.
# shellcheck disable=SC2034  # All color/style variables are used by sourcing scripts
if [[ -n "${NO_COLOR:-}" ]] || [[ ! -t 1 ]]; then
  BOLD='' GREEN='' BLUE='' YELLOW='' RED='' CYAN='' DIM='' NC=''
else
  BOLD='\033[1m'
  GREEN='\033[0;32m'
  BLUE='\033[0;34m'
  YELLOW='\033[1;33m'
  RED='\033[0;31m'
  CYAN='\033[0;36m'
  DIM='\033[2m'
  NC='\033[0m'
fi

info()    { echo -e "${BLUE}→${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
err()     { echo -e "${RED}✗${NC} $*" >&2; }
title()   { echo -e "\n${BOLD}${BLUE}$*${NC}"; }

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

  # confirm_step RESULT_VAR MSG — [Y/n/a]; writes "yes", "no", or "all" to RESULT_VAR.
  # "a" means accept this step and all remaining steps without prompting.
  confirm_step() {
    local _result_var=$1 _msg=$2
    read -r -n 1 -p "$_msg [Y/n/a] " REPLY
    echo
    case "$REPLY" in
      [Nn]) printf -v "$_result_var" '%s' "no"  ;;
      [Aa]) printf -v "$_result_var" '%s' "all" ;;
      *)    printf -v "$_result_var" '%s' "yes" ;;
    esac
  }

  # register_step NAME FN — appends a step to the STEPS array.
  # STEPS must be declared as an array in the calling script before register_step is used.
  register_step() { STEPS+=("${1}|${2}"); }

  # run_steps — prints all registered steps upfront, then runs each with [Y/n/a] confirmation.
  # Steps are read from the global STEPS array (populated via register_step).
  # Prints a summary of ran/skipped counts when complete.
  run_steps() {
    local total=${#STEPS[@]} index=1 ran=0 skipped=0
    local step name fn _accept_all=false _decision

    echo -e "  ${DIM}Steps:${NC}"
    local _i=1
    for step in "${STEPS[@]}"; do
      name="${step%%|*}"
      echo -e "  ${DIM}[$_i/$total] $name${NC}"
      _i=$(( _i + 1 ))
    done
    echo -e "  ${DIM}Y = run · N = skip · A = accept all remaining${NC}"

    for step in "${STEPS[@]}"; do
      name="${step%%|*}"
      fn="${step##*|}"
      echo -e "\n${DIM}[$index/$total]${NC} ${BOLD}$name${NC}"

      if [[ "$_accept_all" == true ]]; then
        $fn
        ran=$(( ran + 1 ))
      else
        confirm_step _decision "  Run this step?"
        case "$_decision" in
          all)
            _accept_all=true
            $fn
            ran=$(( ran + 1 ))
            ;;
          yes)
            $fn
            ran=$(( ran + 1 ))
            ;;
          no)
            echo -e "  ${DIM}⊘ Skipped${NC}"
            skipped=$(( skipped + 1 ))
            ;;
        esac
      fi

      index=$(( index + 1 ))
    done

    echo
    echo -e "${DIM}$ran run · $skipped skipped${NC}"
  }

  # prompt_overwrite FILE — warns that FILE already exists and presents a single combined prompt.
  # [o]verwrite / [b]ackup and overwrite / [s]kip (default: skip)
  # Creates a .backup copy before overwriting when b is chosen.
  # Returns 1 if the user skips, 0 if they choose to overwrite (with or without backup).
  prompt_overwrite() {
    local file=$1
    warn "$file already exists"
    local _choice
    read -rp "  [o]verwrite / [b]ackup and overwrite / [s]kip [s]: " _choice
    case "${_choice:-s}" in
      o|O) ;;
      b|B)
        cp "$file" "${file}.backup"
        echo -e "  ${GREEN}✓${NC} Backed up to ${file}.backup"
        ;;
      *)
        return 1
        ;;
    esac
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
      skip)    _default_hint=", or 0 to skip"                ;;
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

  # conf_get FILE KEY — reads a key = value line from a KEY = VALUE config file.
  # Returns the trimmed value, or empty string if the key is not found.
  conf_get() {
    local file=$1 key=$2
    grep -m1 "^${key}[[:space:]]*=" "$file" 2>/dev/null \
      | sed "s/^${key}[[:space:]]*=[[:space:]]*//"
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

    ln -sfh "$source" "$target"
    echo -e "  ${GREEN}✓${NC} $label"
  }

  # symlink_dir SRC DST [GLOB] [--strip-ext] [--prune]
  # Symlinks all items matching GLOB in SRC into DST, preserving filenames.
  # GLOB defaults to '*'. --strip-ext removes the file extension from the display label.
  # --prune removes stale symlinks in DST that point into SRC but whose source is gone.
  # Inherits SYMLINK_MODE from the environment (pass-through to install_symlink).
  symlink_dir() {
    local src=$1 dst=$2
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
fi
