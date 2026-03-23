#!/bin/bash
# Git configuration setup — delegates to git/steps.sh.
#
# Usage: bash git/setup.sh
#        (also sourced by install.sh and bin/otto-workbench for step functions)
_D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# When run directly, exec steps.sh so its standalone guard fires.
# When sourced, fall through to the dot-source below so functions are available.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  exec bash "$_D/steps.sh" "$@"
fi
# shellcheck source=git/steps.sh
. "$_D/steps.sh"
