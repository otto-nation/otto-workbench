#!/usr/bin/env bash
# Output helpers — colors, logging, and portable sed.
#
# Works in both bash and zsh, so it uses no bash-only features: the zsh
# component's own scripts source it, and the facade sources it outside the
# bash-only guard for that reason.
#
# The color variables — `BOLD`, `GREEN`, `BLUE`, `YELLOW`, `RED`, `CYAN`, `DIM`,
# `NC` — are exported alongside the functions for callers that format their own
# output.

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

# indent PREFIX — copy stdin to stdout with PREFIX at the start of every line.
# PREFIX is written literally, so it is spaces for a nested block of output and
# a marker word for a machine-readable one.
#
# A final line arriving without a trailing newline comes back the same way on
# BSD sed and terminated on GNU sed. Callers redirect to a terminal or sit in a
# command substitution, and neither can tell the difference.
#
# ShellCheck reads the anchored substitution as SC2001 and suggests
# `${var//x/y}`, which cannot express it: parameter expansion has no line
# anchor, so the equivalent is a newline-to-newline-plus-prefix replacement that
# still has to prepend the first line by hand.
#
# ceiling: PREFIX reaches sed as a replacement without escaping, so a `/`, `&`,
# or backslash in it would be read as syntax rather than text. Every caller
# passes spaces or a plain word. Upgrade trigger: escape the replacement if a
# caller ever needs to indent with a path or a regex metacharacter.
indent() { sed "s/^/$1/"; }

# info MESSAGE — blue info message with an arrow.
info()    { echo -e "${BLUE}→${NC} $*"; }

# success MESSAGE — green success message with a checkmark.
success() { echo -e "${GREEN}✓${NC} $*"; }

# warn MESSAGE — yellow warning; also logged to WORKBENCH_INSTALL_LOG.
warn() {
  echo -e "${YELLOW}⚠${NC}  $*"
  [[ -n "${WORKBENCH_INSTALL_LOG:-}" ]] && echo "WARN:${WORKBENCH_CURRENT_COMPONENT:+[$WORKBENCH_CURRENT_COMPONENT] }$*" >> "$WORKBENCH_INSTALL_LOG" || true
}
# err MESSAGE — red error to stderr; also logged to WORKBENCH_INSTALL_LOG.
err() {
  echo -e "${RED}✗${NC} $*" >&2
  [[ -n "${WORKBENCH_INSTALL_LOG:-}" ]] && echo "ERR:${WORKBENCH_CURRENT_COMPONENT:+[$WORKBENCH_CURRENT_COMPONENT] }$*" >> "$WORKBENCH_INSTALL_LOG" || true
}

# title TEXT — bold blue section header.
title()   { echo -e "\n${BOLD}${BLUE}$*${NC}"; }

# skip [label] — print a skip line with optional label
skip() { echo -e "${DIM}⊘ ${1:-Skipped}${NC}"; }

# print_version SCRIPT_NAME [COMPONENT_KEY] — print tool and workbench version.
# Reads from .github/.release-please-manifest.json in WORKBENCH_DIR.
print_version() {
  local name="$1"
  local component_key="${2:-ai/claude}"
  local manifest="${WORKBENCH_DIR}/.github/.release-please-manifest.json"
  local tool_version="unknown" workbench_version="unknown" sha _versions
  _versions=$(jq -r --arg k "$component_key" '.[$k] // "unknown", .["."] // "unknown"' "$manifest" 2>/dev/null) && {
    tool_version="${_versions%%$'\n'*}"
    workbench_version="${_versions#*$'\n'}"
  }
  sha=$(git -C "$WORKBENCH_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")
  echo "$name $tool_version"
  echo "otto-workbench $workbench_version ($sha)"
}

# sync_header LABEL — section header for sync steps. Suppressed during sync.
sync_header() { [[ "${WORKBENCH_SYNC:-}" == true ]] || { echo; info "$@"; }; }

# Summary status lines — indented for use in post-install/sync summaries.
# Each prints: "  <icon> <message>"
# Complement success()/warn()/err() which are for step output during install.

# summary_section LABEL — cyan section header for summaries. Suppressed during sync.
summary_section() {
  [[ "${WORKBENCH_SYNC:-}" == true ]] && return
  echo; echo -e "  ${CYAN}$*${NC}"
}
# summary_ok MESSAGE — indented success line. Suppressed during sync.
summary_ok() { [[ "${WORKBENCH_SYNC:-}" == true ]] && return; echo -e "  ${GREEN}✓${NC} $*"; }

# summary_warn MESSAGE — indented warning; logged instead of printed during sync.
summary_warn() {
  if [[ "${WORKBENCH_SYNC:-}" == true && -n "${WORKBENCH_INSTALL_LOG:-}" ]]; then
    echo "WARN:${WORKBENCH_CURRENT_COMPONENT:+[$WORKBENCH_CURRENT_COMPONENT] }$*" >> "$WORKBENCH_INSTALL_LOG"
  else
    echo -e "  ${YELLOW}⚠${NC}  $*"
  fi
}
# summary_err MESSAGE — indented error line. Printed even during sync.
summary_err()  { echo -e "  ${RED}✗${NC} $*"; }

# summary_info MESSAGE — indented dim detail line. Suppressed during sync.
summary_info() { [[ "${WORKBENCH_SYNC:-}" == true ]] && return; echo -e "  ${DIM}●${NC} $*"; }
