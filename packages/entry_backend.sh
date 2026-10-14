#!/usr/bin/env bash
# Package backend for every machine but macOS-with-Homebrew — installs each
# manifest entry with install_package, which uses Homebrew (Linuxbrew) where it
# exists and mise otherwise. macos-only entries and casks are listed as
# unavailable; git entries belong to the zsh component. Sourced by
# packages/setup.sh.

pkg_backend_init() { :; }

# pkg_is_installed TYPE FULL_NAME — 0 when the entry's binary already runs.
# macos-only and git entries are never installed here; status shows – for them.
pkg_is_installed() {
  local -A __p=()
  package_lookup "$2" __p || return 1
  [[ "${__p[disposition]}" == macos-only || "${__p[disposition]}" == git ]] && return 1
  _cmd_runnable "${__p[cmd]}"
}

# pkg_skip_reason TYPE FULL_NAME — 0 when this backend will not install it
# (status display shows – rather than +). Prints the reason for the status line.
pkg_skip_reason() {
  local -A __p=()
  package_lookup "$2" __p || return 1
  case "${__p[disposition]}" in
    macos-only)
      echo "macOS only"
      return 0
      ;;
    git)
      echo "installed by the zsh component"
      return 0
      ;;
    system)
      if ! command -v brew >/dev/null 2>&1; then
        echo "distro package"
        return 0
      fi
      ;;
  esac
  return 1
}

# pkg_install_one TYPE FULL_NAME — installs one manifest entry through
# install_package, or reports why this backend will not. macos-only and git are
# skips (return 0). A failed install is a failure (return 1), and so is an entry
# with nothing to install it — a system package on a machine without Homebrew,
# reported with the distro hint.
pkg_install_one() {
  local -A __p=()
  if ! package_lookup "$2" __p; then
    warn "$2 not in any manifest"
    return 1
  fi
  case "${__p[disposition]}" in
    macos-only)
      echo -e "  ${DIM}– ${__p[name]}: macOS only${NC}"
      return 0
      ;;
    git)
      echo -e "  ${DIM}– ${__p[name]}: installed by the zsh component${NC}"
      return 0
      ;;
    *)
      install_package "${__p[name]}"
      ;;
  esac
}

# pkg_install_file FILE LABEL — installs every entry in FILE. Returns 1 when an
# install failed or a system entry is missing; macos-only and git skips
# are not failures.
pkg_install_file() {
  local file="$1"
  local -a names
  package_entries "$file" names
  local n failed=0
  local -A __p
  for n in "${names[@]}"; do
    __p=()
    if ! package_lookup "$n" __p; then
      warn "$n not in any manifest"
      failed=1
      continue
    fi
    if ! pkg_install_one "${__p[type]}" "${__p[formula]}"; then
      failed=1
    fi
  done
  if [[ "$failed" -ne 0 ]]; then
    return 1
  fi
  return 0
}

# pkg_all_installed FILE — 0 when every entry this backend would install is
# already present. macos-only and git entries are owned elsewhere and skipped.
# A system entry with no Homebrew is reported at install time but does not
# count as missing here, so a distro package cannot keep the core prompt open.
pkg_all_installed() {
  local -a names
  package_entries "$1" names
  local n
  local -A __p
  for n in "${names[@]}"; do
    __p=()
    package_lookup "$n" __p || return 1
    [[ "${__p[disposition]}" == macos-only || "${__p[disposition]}" == git ]] && continue
    if [[ "${__p[disposition]}" == system ]] && ! command -v brew >/dev/null 2>&1; then
      continue
    fi
    pkg_is_installed "${__p[type]}" "${__p[formula]}" || return 1
  done
  return 0
}

# pkg_backend_finish — no aggregated system-package list: the installer helper
# and pkg_install_one already warn at install time; repeating them here would
# double-report.
pkg_backend_finish() {
  return 0
}
