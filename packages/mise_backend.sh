#!/usr/bin/env bash
# Package backend for machines without Homebrew — installs each manifest entry
# with install_package (mise), reports linux: system entries it cannot install,
# and lists macos-only entries as unavailable. Sourced by packages/setup.sh.

pkg_backend_init() { :; }

# pkg_is_installed TYPE FULL_NAME — 0 when the entry's binary already runs.
# macos-only entries are never installed here; git entries are owned by zsh.
pkg_is_installed() {
  local -A __p=()
  package_lookup "$2" __p || return 1
  [[ "${__p[disposition]}" == macos-only ]] && return 1
  [[ "${__p[disposition]}" == git ]] && return 0
  _cmd_runnable "${__p[cmd]}"
}

# pkg_skip_reason TYPE FULL_NAME — 0 when the mise backend will not install it
# (status display shows – rather than +).
pkg_skip_reason() {
  local -A __p=()
  package_lookup "$2" __p || return 1
  [[ "${__p[disposition]}" == macos-only || "${__p[disposition]}" == git ]]
}

pkg_install_one() {
  local -A __p=()
  if ! package_lookup "$2" __p; then
    warn "$2 not in any manifest"
    return 1
  fi
  case "${__p[disposition]}" in
    macos-only)
      echo -e "  ${DIM}– ${__p[name]}: macOS only${NC}"
      return 1
      ;;
    git)
      echo -e "  ${DIM}– ${__p[name]}: installed by the zsh component${NC}"
      return 1
      ;;
    system)
      if _cmd_runnable "${__p[cmd]}"; then
        return 0
      fi
      # Same wording as install_package; do not also aggregate in finish.
      warn "${__p[name]} is a system package — install it with your distro's package manager"
      return 1
      ;;
    mise)
      install_package "${__p[formula]}"
      ;;
    *)
      warn "${__p[name]} has no Linux install (${__p[disposition]:-none})"
      return 1
      ;;
  esac
}

pkg_install_file() {
  local file="$1"
  local -a names
  package_entries "$file" names
  local n
  for n in "${names[@]}"; do
    pkg_install_one brew "$n" || true
  done
}

pkg_all_installed() {
  local -a names
  package_entries "$1" names
  local n
  local -A __p
  for n in "${names[@]}"; do
    __p=()
    package_lookup "$n" __p || return 1
    [[ "${__p[disposition]}" == macos-only || "${__p[disposition]}" == git ]] && continue
    pkg_is_installed brew "$n" || return 1
  done
  return 0
}

pkg_backend_finish() {
  # System-package warnings are printed at install time by pkg_install_one
  # (and by the installer helper for any entry routed there). Aggregating
  # them here would double-report.
  return 0
}
