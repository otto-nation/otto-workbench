#!/usr/bin/env bash
# User interaction helpers — confirmation prompts, menus, and config reading.
# Bash-only (read -n 1 behaves differently in zsh).
#
# Functions: confirm, confirm_n, confirm_step, prompt_overwrite,
#            select_menu, select_subdirs, conf_get

[[ -n "${_LIB_PROMPTS_SH:-}" ]] && return
_LIB_PROMPTS_SH=1

# Ensure output helpers are available
_prompts_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=output.sh
. "$_prompts_lib_dir/output.sh"
unset _prompts_lib_dir

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
  local -n __out=$1
  local _msg=$2
  read -r -n 1 -p "$_msg [Y/n/a] " REPLY
  echo
  case "$REPLY" in
    [Nn]) __out="no"  ;;
    [Aa]) __out="all" ;;
    *)    __out="yes" ;;
  esac
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
  local -n __out=$1
  local _count=$2
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

  local _raw _selected _invalid _num _all _i
  while true; do
    read -rp "  ${_number_hint}${_default_hint}: " _raw
    echo

    # Treat whitespace-only input the same as empty.
    if [[ -z "${_raw// /}" ]]; then
      case "$_default" in
        all)
          _all=""
          for (( _i=1; _i<=_count; _i++ )); do _all+="$_i "; done
          __out="${_all% }"
          return 0 ;;
        skip)
          __out=""
          skip
          return 0 ;;
        require)
          __out=""
          return 1 ;;
      esac
    fi

    if [[ "$_raw" == "0" ]]; then
      __out=""
      skip
      return 0
    fi

    _selected="" _invalid=false
    for _num in $_raw; do
      if [[ "$_num" =~ ^[0-9]+$ ]] && (( _num >= 1 && _num <= _count )); then
        _selected+="$_num "
        [[ "$_single" == true ]] && break
      else
        warn "Unknown option: $_num"
        _invalid=true
      fi
    done

    if [[ "$_invalid" == true ]]; then
      warn "Please enter valid numbers between 1 and $_count (or 0 to skip)"
      continue
    fi

    __out="${_selected% }"
    return 0
  done
}

# select_subdirs RESULT_VAR PARENT_DIR PROMPT [SELECT_MENU_OPTS...]
#
# Discovers subdirectories in PARENT_DIR that contain setup.sh, presents a
# numbered menu with PROMPT, and writes space-separated selected names to
# RESULT_VAR. Any extra arguments are forwarded to select_menu (e.g. --default
# all, --single). Returns 1 if no subdirectories are found.
select_subdirs() {
  local -n __out=$1
  local _parent_dir=$2 _prompt=$3
  shift 3

  local _items=() _dir
  for _dir in "$_parent_dir"/*/; do
    [[ -f "${_dir}setup.sh" ]] && _items+=("$(basename "$_dir")")
  done

  if [[ ${#_items[@]} -eq 0 ]]; then
    err "No setups found in $_parent_dir"
    return 1
  fi

  info "$_prompt"
  local _i=1 _item
  for _item in "${_items[@]}"; do
    echo "  [$_i] $_item"
    _i=$(( _i + 1 ))
  done
  echo

  local _sm_result
  select_menu _sm_result "${#_items[@]}" "$@"

  if [[ -z "$_sm_result" ]]; then
    __out=""
    return 0
  fi

  local _selected="" _num
  for _num in $_sm_result; do
    _selected+="${_items[$(( _num - 1 ))]} "
  done
  __out="${_selected% }"
}

# conf_get FILE KEY — reads a key = value line from a KEY = VALUE config file.
# Returns the trimmed value, or empty string if the key is not found.
conf_get() {
  local file=$1 key=$2
  grep -m1 "^${key}[[:space:]]*=" "$file" 2>/dev/null \
    | sed "s/^${key}[[:space:]]*=[[:space:]]*//"
}
