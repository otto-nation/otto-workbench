#!/usr/bin/env bash
# Package manifests — the Brewfiles under packages/ and the install identity
# their trailing annotations declare. One line owns each package: its formula,
# how it installs without Homebrew, and the binary it provides.
#
# Annotation grammar, after `#` on a brew line, `;`-separated:
#   mise: SPEC | linux: system | macos-only | git: URL   (exactly one disposition)
#   cmd: BIN                                             (optional)
# Cask lines are implicitly macos-only.

[[ -n "${_LIB_PACKAGES_SH:-}" ]] && return
_LIB_PACKAGES_SH=1

# package_manifests OUT — fills array OUT with every *.Brewfile under
# $PACKAGES_DIR, recursively, sorted.
package_manifests() {
  local -n __pm_out=$1
  __pm_out=()
  [[ -d "${PACKAGES_DIR:-}" ]] || return 0
  local f _list
  _list=$(find "$PACKAGES_DIR" \( -name '*.Brewfile' -o -name Brewfile \) -type f | LC_ALL=C sort) || true
  while IFS= read -r f || [[ -n "$f" ]]; do
    if [[ -z "$f" ]]; then
      continue
    fi
    __pm_out+=("$f")
  done <<< "$_list"
  return 0
}

# _package_parse_line LINE OUT — parses one manifest line into assoc OUT;
# returns 1 for a line that is not a brew/cask entry.
_package_parse_line() {
  local __pl_line="$1"
  local -n __pl=$2
  [[ "$__pl_line" =~ ^(brew|cask)[[:space:]]+\"([^\"]+)\"[^#]*(#(.*))?$ ]] || return 1
  __pl=(["type"]="${BASH_REMATCH[1]}" ["formula"]="${BASH_REMATCH[2]}"
        ["name"]="${BASH_REMATCH[2]##*/}" ["mise"]="" ["git"]="" ["cmd"]=""
        ["disposition"]="" ["unknown"]="" ["empty"]="" ["cask_annotation"]="")
  local __pl_ann="${BASH_REMATCH[4]}"
  __pl_ann="${__pl_ann#"${__pl_ann%%[![:space:]]*}"}"
  __pl_ann="${__pl_ann%"${__pl_ann##*[![:space:]]}"}"
  local __pl_part __pl_key __pl_val
  local -a __pl_parts=()
  IFS=';' read -ra __pl_parts <<< "$__pl_ann"
  for __pl_part in "${__pl_parts[@]}"; do
    __pl_part="${__pl_part#"${__pl_part%%[![:space:]]*}"}"
    __pl_part="${__pl_part%"${__pl_part##*[![:space:]]}"}"
    if [[ -z "$__pl_part" ]]; then
      continue
    fi
    if [[ "$__pl_part" == macos-only ]]; then
      __pl["disposition"]+="macos-only "
      continue
    fi
    __pl_key="${__pl_part%%:*}"
    __pl_val="${__pl_part#*:}"
    __pl_key="${__pl_key#"${__pl_key%%[![:space:]]*}"}"
    __pl_key="${__pl_key%"${__pl_key##*[![:space:]]}"}"
    __pl_val="${__pl_val#"${__pl_val%%[![:space:]]*}"}"
    __pl_val="${__pl_val%"${__pl_val##*[![:space:]]}"}"
    if [[ "$__pl_key" == "$__pl_part" ]]; then
      __pl["unknown"]+="$__pl_part "
      continue
    fi
    case "$__pl_key" in
      mise)
        [[ -n "$__pl_val" ]] || __pl["empty"]+="mise "
        __pl["mise"]="$__pl_val"
        __pl["disposition"]+="mise "
        ;;
      git)
        [[ -n "$__pl_val" ]] || __pl["empty"]+="git "
        __pl["git"]="$__pl_val"
        __pl["disposition"]+="git "
        ;;
      cmd)
        [[ -n "$__pl_val" ]] || __pl["empty"]+="cmd "
        __pl["cmd"]="$__pl_val"
        ;;
      linux)
        [[ "$__pl_val" == system ]] || { __pl["unknown"]+="$__pl_part "; continue; }
        __pl["disposition"]+="system "
        ;;
      *)
        __pl["unknown"]+="$__pl_part "
        ;;
    esac
  done
  if [[ "${__pl[type]}" == cask && -n "$__pl_ann" ]]; then
    __pl["cask_annotation"]=1
  fi
  if [[ "${__pl[type]}" == cask ]]; then
    __pl["mise"]=""
    __pl["git"]=""
    __pl["disposition"]="macos-only "
  fi
  __pl["disposition"]="${__pl[disposition]% }"
  __pl["unknown"]="${__pl[unknown]% }"
  __pl["empty"]="${__pl[empty]% }"
  if [[ -z "${__pl[cmd]}" ]]; then
    __pl["cmd"]="${__pl[name]}"
  fi
}

# package_lookup NAME OUT — fills assoc OUT with the manifest entry for NAME
# (full formula or its short name) plus OUT[manifest]. Returns 1 when NAME is
# in no manifest under $PACKAGES_DIR.
#
# ceiling: each lookup re-walks every Brewfile under PACKAGES_DIR. Status,
# all-installed, and install each call this per entry.
# Upgrade trigger: once a single packages/setup.sh run is slow enough to notice.
package_lookup() {
  local __lk_name="$1"
  local __lk_out="$2"
  local -n __lk=$__lk_out
  local -a __lk_files
  package_manifests __lk_files
  local __lk_f __lk_line
  for __lk_f in "${__lk_files[@]}"; do
    while IFS= read -r __lk_line || [[ -n "$__lk_line" ]]; do
      _package_parse_line "$__lk_line" "$__lk_out" || continue
      [[ "${__lk[formula]}" == "$__lk_name" || "${__lk[name]}" == "$__lk_name" ]] || continue
      __lk["manifest"]="$__lk_f"
      return 0
    done < "$__lk_f"
  done
  __lk=()
  return 1
}

# package_entries FILE OUT — fills array OUT with FILE's short names in order.
package_entries() {
  local file="$1"
  local -n __pe=$2
  __pe=()
  local line
  declare -A __ent=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    _package_parse_line "$line" __ent || continue
    __pe+=("${__ent[name]}")
  done < "$file"
  return 0
}

# packages_use_bundle — 0 when package stacks install with `brew bundle`: macOS
# with Homebrew on PATH. Everywhere else stacks install entry by entry through
# install_package, which still prefers Homebrew (Linuxbrew) where it exists but
# skips the macOS-only entries and casks a Linux brew cannot install. Reads
# `uname -s` rather than $OSTYPE, which bash sets itself and a caller cannot vary.
#
# step_packages_brew_install and cmd_install still match $OSTYPE deliberately:
# they are the macOS Homebrew bootstrap, not stack-install dispatch.
packages_use_bundle() {
  [[ "$(uname -s)" == Darwin ]] || return 1
  command -v brew >/dev/null 2>&1
}

# install_package NAME [--version V] — installs the manifest package NAME with
# Homebrew where it exists and otherwise with mise (`mise use -g SPEC[@V]`),
# bootstrapping mise first when neither is present. Skipped when the entry's
# binary already runs; a mise shim with no active version does not count.
# Returns 1 when NAME is in no manifest, has no mise spec on a machine without
# Homebrew, or the install fails; 2 on a usage error, including --version on a
# spec that already contains '@'.
#
# V pins the mise path only: Homebrew installs its current formula, and an entry
# with no mise spec has nothing to pin. Refused (return 2) when the spec already
# contains '@'; the pin would otherwise produce SPEC@EXISTING@V.
install_package() {
  local name="${1:-}" version=""
  [[ -n "$name" && "$name" != -* ]] || { err "usage: install_package NAME [--version V]"; return 2; }
  if (( $# > 1 )); then
    [[ "$2" == --version ]] || { err "install_package: unknown argument: $2"; return 2; }
    [[ -n "${3:-}" ]] || { err "install_package: --version requires a value"; return 2; }
    (( $# == 3 )) || { err "install_package: unexpected argument: $4"; return 2; }
    version="$3"
  fi
  local -A __ip=()
  if ! package_lookup "$name" __ip; then
    local -a __m; package_manifests __m
    warn "install_package: $name is in no manifest (searched: ${__m[*]:-no manifests under $PACKAGES_DIR})"
    return 1
  fi
  local spec="${__ip[mise]}"
  if [[ -n "$spec" && -n "$version" && "$spec" == *@* ]]; then
    err "install_package: $name already pins a version in its mise spec ($spec); omit --version"
    return 2
  fi
  if [[ -n "$spec" && -n "$version" ]]; then
    spec="$spec@$version"
  fi
  _install_brew_or_mise "${__ip[cmd]}" "${__ip[formula]}" "$spec" "$name" "${__ip[disposition]}"
}
