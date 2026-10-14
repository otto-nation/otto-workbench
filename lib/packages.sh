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
  local line="$1"
  local -n __pl=$2
  [[ "$line" =~ ^(brew|cask)[[:space:]]+\"([^\"]+)\"[^#]*(#(.*))?$ ]] || return 1
  __pl=(["type"]="${BASH_REMATCH[1]}" ["formula"]="${BASH_REMATCH[2]}"
        ["name"]="${BASH_REMATCH[2]##*/}" ["mise"]="" ["git"]="" ["cmd"]="" ["disposition"]="")
  local ann="${BASH_REMATCH[4]}"
  local part key val
  local -a parts=()
  IFS=';' read -ra parts <<< "$ann"
  for part in "${parts[@]}"; do
    part="${part#"${part%%[![:space:]]*}"}"
    part="${part%"${part##*[![:space:]]}"}"
    if [[ -z "$part" ]]; then
      continue
    fi
    if [[ "$part" == macos-only ]]; then
      __pl["disposition"]+="macos-only "
      continue
    fi
    key="${part%%:*}"
    val="${part#*:}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    if [[ "$key" == mise ]]; then
      __pl["mise"]="$val"
      __pl["disposition"]+="mise "
    elif [[ "$key" == linux && "$val" == system ]]; then
      __pl["disposition"]+="system "
    elif [[ "$key" == git ]]; then
      __pl["git"]="$val"
      __pl["disposition"]+="git "
    elif [[ "$key" == cmd ]]; then
      __pl["cmd"]="$val"
    fi
  done
  if [[ "${__pl[type]}" == cask ]]; then
    __pl["disposition"]="macos-only "
  fi
  __pl["disposition"]="${__pl[disposition]% }"
  if [[ -z "${__pl[cmd]}" ]]; then
    __pl["cmd"]="${__pl[name]}"
  fi
}

# package_lookup NAME OUT — fills assoc OUT with the manifest entry for NAME
# (full formula or its short name) plus OUT[manifest]. Returns 1 when NAME is
# in no manifest under $PACKAGES_DIR.
package_lookup() {
  local name="$1"
  local out="$2"
  local -n __lk=$out
  local -a files
  package_manifests files
  local f line
  for f in "${files[@]}"; do
    while IFS= read -r line || [[ -n "$line" ]]; do
      _package_parse_line "$line" "$out" || continue
      [[ "${__lk[formula]}" == "$name" || "${__lk[name]}" == "$name" ]] || continue
      __lk["manifest"]="$f"
      return 0
    done < "$f"
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

# install_package NAME [--version V] — installs the manifest package NAME with
# Homebrew where it exists and otherwise with mise (`mise use -g SPEC[@V]`),
# bootstrapping mise first when neither is present. Skipped when the entry's
# binary already runs; a mise shim with no active version does not count.
# Returns 1 when NAME is in no manifest, has no mise spec on a machine without
# Homebrew, or the install fails; 2 on a usage error.
#
# V pins the mise path only: Homebrew installs its current formula, and an entry
# with no mise spec has nothing to pin. V is appended to the manifest spec as
# given, so it is for entries whose spec carries no version of its own.
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
  [[ -n "$spec" && -n "$version" ]] && spec="$spec@$version"
  _install_brew_or_mise "${__ip[cmd]}" "${__ip[formula]}" "$spec" "$name" "${__ip[disposition]}"
}
