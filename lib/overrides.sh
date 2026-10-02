#!/usr/bin/env bash
# User-override helpers for AI agents, skills, and rules — list, copy, disable,
# enable, and status against the defaults under the workbench tree.
#
# Sourced by `bin/otto-workbench`. Reads path constants (`USER_*`, `CLAUDE_*`,
# `SKILLS_*`, `GUIDELINES_*`) and colour vars from `lib/ui.sh`; nothing from
# CLI-local state.

[[ -n "${_LIB_OVERRIDES_SH:-}" ]] && return
_LIB_OVERRIDES_SH=1

_override_type_dirs() {
  local -n __default=$2 __user=$3
  case "$1" in
    agent|agents)   __default="$CLAUDE_AGENTS_SRC_DIR"; __user="$USER_AGENTS_DIR" ;;
    skill|skills)   __default="$SKILLS_SRC_DIR"; __user="$USER_SKILLS_DIR" ;;
    rule|rules)     __default="$GUIDELINES_RULES_SRC_DIR"; __user="$USER_RULES_DIR" ;;
    *)              err "Unknown type: $1 (expected: agent, skill, rule)"; return 1 ;;
  esac
}

_is_override_type() {
  case "$1" in
    agent|agents|skill|skills|rule|rules) return 0 ;;
    *) return 1 ;;
  esac
}

_show_override_status() {
  local dir="$1" type="$2" found=false item name
  for item in "$dir"/*; do
    [[ -e "$item" ]] || continue
    name=$(basename "$item")
    [[ "$found" == false ]] && { echo -e "  ${CYAN}$type${NC}"; found=true; }
    [[ "$name" == *.disabled ]] \
      && echo -e "    ${RED}✗${NC} ${name%.disabled} ${DIM}(disabled)${NC}" \
      || echo -e "    ${GREEN}↻${NC} ${name%.md} ${DIM}(override)${NC}"
  done
}

_list_override_dir() {
  local dir="$1" label="$2" _found=false
  for item in "$dir"/*; do
    [[ -e "$item" ]] || continue
    _found=true
    local name suffix=""
    name=$(basename "$item")
    [[ "$name" == *.disabled ]] && suffix=" (disabled)"
    echo -e "  ${CYAN}$label/${name}${NC}${DIM}${suffix}${NC}"
  done
  [[ "$_found" == true ]]
}

_show_item_status() {
  local type="$1" name="$2"
  local default_dir user_dir
  _override_type_dirs "$type" default_dir user_dir

  local has_default=false has_override=false is_disabled=false
  case "$type" in
    skill|skills) if [[ -d "$default_dir/$name" ]]; then has_default=true; fi ;;
    *)            if [[ -f "$default_dir/${name}.md" ]]; then has_default=true; fi ;;
  esac
  case "$type" in
    skill|skills) if [[ -d "$user_dir/$name" ]]; then has_override=true; fi ;;
    *)            if [[ -f "$user_dir/${name}.md" ]]; then has_override=true; fi ;;
  esac
  [[ -f "$user_dir/${name}.disabled" ]] && is_disabled=true

  if [[ "$is_disabled" == true ]]; then
    echo -e "  ${RED}✗${NC} ${name} ${DIM}(disabled)${NC}"
  elif [[ "$has_override" == true ]]; then
    echo -e "  ${GREEN}↻${NC} ${name} ${DIM}(override)${NC}"
  elif [[ "$has_default" == true ]]; then
    echo -e "  ${DIM}${name} (default)${NC}"
  else
    err "No $type named '$name'"
    return 1
  fi
}

_list_defaults() {
  local type="$1"
  local default_dir user_dir
  _override_type_dirs "$type" default_dir user_dir

  local item name
  for item in "$default_dir"/*; do
    [[ -e "$item" ]] || continue
    name=$(basename "$item")
    case "$type" in
      agent|agents|rule|rules) name="${name%.md}" ;;
    esac
    if [[ -f "$user_dir/${name}.disabled" ]]; then
      echo -e "  ${RED}✗${NC} ${name} ${DIM}(disabled)${NC}"
    elif { [[ "$type" == skill* ]] && [[ -d "$user_dir/$name" ]]; } \
      || { [[ "$type" != skill* ]] && [[ -f "$user_dir/${name}.md" ]]; }; then
      echo -e "  ${GREEN}↻${NC} ${name} ${DIM}(override)${NC}"
    else
      echo -e "  ${DIM}${name}${NC}"
    fi
  done
}

_ai_override_usage() {
  cat <<EOF
Usage: otto-workbench ai override <agent|skill|rule> [name] [--add|--disable|--enable|--status]

Manage user overrides for AI config (agents, skills, rules).
Override files live in ~/.config/workbench/overrides/ai/.

Examples:
  otto-workbench ai override agent                   List agents and their status
  otto-workbench ai override agent debugger           Show debugger override status
  otto-workbench ai override agent debugger --add     Copy default for editing
  otto-workbench ai override agent debugger --disable Suppress a default
  otto-workbench ai override agent debugger --enable  Re-enable a disabled default

Flags:
  --status    Show detailed override status (overrides vs defaults)
  --help      Show this help
EOF
}

_ai_override_list() {
  local display_dir="${USER_AI_DIR/#$HOME/\~}"
  _ai_override_usage
  echo
  info "Active user overrides"
  local found=false item
  for item in "$USER_GUIDELINES_SRC" "$USER_GUIDELINES_LOCAL" "$USER_SETTINGS_SRC"; do
    [[ -f "$item" ]] || continue
    found=true
    echo -e "  ${CYAN}$(basename "$item")${NC}"
  done
  local dir label
  for dir in "$USER_AGENTS_DIR:agents" "$USER_SKILLS_DIR:skills" "$USER_RULES_DIR:rules"; do
    label="${dir##*:}"
    dir="${dir%%:*}"
    [[ -d "$dir" ]] || continue
    if _list_override_dir "$dir" "$label"; then
      found=true
    fi
  done
  [[ "$found" == false ]] && echo -e "  ${DIM}No overrides in ${display_dir}/${NC}"
  return 0
}

_ai_override_add() {
  local type="$1" name="$2" display_dir="${USER_AI_DIR/#$HOME/\~}"
  local default_dir user_dir
  _override_type_dirs "$type" default_dir user_dir

  local src=""
  case "$type" in
    agent|agents) src="$default_dir/${name}.md" ;;
    rule|rules)   src="$default_dir/${name}.md" ;;
    skill|skills) src="$default_dir/${name}" ;;
  esac

  if [[ ! -e "$src" ]]; then
    err "No default $type named '$name' found at $src"
    return 1
  fi

  mkdir -p "$user_dir"
  if [[ "$type" == skill* ]]; then
    cp -R "$src" "$user_dir/"
    success "Copied $type '$name' to ${display_dir}/ for editing"
    echo -e "  ${DIM}$user_dir/$name/${NC}"
  else
    local dest="$user_dir/${name}.md"
    cp "$src" "$dest"
    success "Copied $type '$name' to ${display_dir}/ for editing"
    echo -e "  ${DIM}$dest${NC}"
  fi
  echo -e "  ${DIM}Run 'otto-workbench sync' after editing to apply${NC}"
}

_ai_override_disable() {
  local type="$1" name="$2"
  local default_dir user_dir
  _override_type_dirs "$type" default_dir user_dir
  mkdir -p "$user_dir"
  touch "$user_dir/${name}.disabled"
  success "Disabled $type '$name'"
  echo -e "  ${DIM}Run 'otto-workbench sync' to apply${NC}"
}

_ai_override_enable() {
  local type="$1" name="$2"
  local default_dir user_dir
  _override_type_dirs "$type" default_dir user_dir
  local sentinel="$user_dir/${name}.disabled"
  if [[ -f "$sentinel" ]]; then
    rm "$sentinel"
    success "Re-enabled $type '$name'"
    echo -e "  ${DIM}Run 'otto-workbench sync' to apply${NC}"
  else
    warn "No disable sentinel found for $type '$name'"
  fi
}

_ai_override_set_positional() {
  local -n __t=$1 __n=$2
  local val="$3"
  if [[ -z "$__t" ]]; then __t="$val"
  elif [[ -z "$__n" ]]; then __n="$val"
  else err "Unexpected argument: $val"; _ai_override_usage >&2; return 1
  fi
}

_ai_override_parse_args() {
  local -n __type=$1 __name=$2 __action=$3
  shift 3
  __type="" __name="" __action=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --add|--disable|--enable|--status)
        [[ -n "$__action" ]] && { err "Only one action flag allowed (got $__action and $1)"; return 1; }
        __action="$1"
        ;;
      -h|--help)    __action="--help" ;;
      -*)           err "Unknown flag: $1"; _ai_override_usage >&2; return 1 ;;
      *)            _ai_override_set_positional __type __name "$1" || return 1 ;;
    esac
    shift
  done
}

_ai_override_status() {
  info "Override status (${USER_AI_DIR/#$HOME/\~}/ vs defaults)"
  echo
  local _type default_dir user_dir
  for _type in agents skills rules; do
    _override_type_dirs "$_type" default_dir user_dir
    [[ -d "$user_dir" ]] || continue
    _show_override_status "$user_dir" "$_type"
  done
}
