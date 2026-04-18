#!/usr/bin/env bash
# Output helpers — colors, logging, and portable sed.
# Works in both bash and zsh (no bash-only features).
#
# Functions: info, success, warn, err, title, skip, sed_i
# Variables: BOLD, GREEN, BLUE, YELLOW, RED, CYAN, DIM, NC

[[ -n "${_LIB_OUTPUT_SH:-}" ]] && return
_LIB_OUTPUT_SH=1

# Bash 4.3+ required for namerefs (local -n) used throughout workbench libs.
# macOS ships bash 3.2 at /bin/bash — if env bash resolves there, fail early.
# Guard is bash-only so zsh sourcing (e.g. zsh/bin/aliases) is unaffected.
if [[ -n "${BASH_VERSINFO[0]:-}" ]]; then
  if (( BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 3) )); then
    echo "ERROR: Bash 4.3+ required (found ${BASH_VERSION}). Install: brew install bash" >&2
    return 1 2>/dev/null || exit 1
  fi
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

# sed_i EXPRESSION FILE — portable in-place sed (macOS and Linux).
# shellcheck disable=SC2145  # ${@: -1} extracts the last argument (the file), not a join
sed_i() { sed -i.bak "$@" && rm -f "${@: -1}.bak"; }

info()    { echo -e "${BLUE}→${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn() {
  echo -e "${YELLOW}⚠${NC}  $*"
  [[ -n "${WORKBENCH_INSTALL_LOG:-}" ]] && echo "WARN:${WORKBENCH_CURRENT_COMPONENT:+[$WORKBENCH_CURRENT_COMPONENT] }$*" >> "$WORKBENCH_INSTALL_LOG"
}
err() {
  echo -e "${RED}✗${NC} $*" >&2
  [[ -n "${WORKBENCH_INSTALL_LOG:-}" ]] && echo "ERR:${WORKBENCH_CURRENT_COMPONENT:+[$WORKBENCH_CURRENT_COMPONENT] }$*" >> "$WORKBENCH_INSTALL_LOG"
}
title()   { echo -e "\n${BOLD}${BLUE}$*${NC}"; }

# skip [label] — print a skip line with optional label
skip() { echo -e "${DIM}⊘ ${1:-Skipped}${NC}"; }
