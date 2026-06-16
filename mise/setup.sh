#!/usr/bin/env bash
# Interactive mise installer — delegates to step_mise_install in steps.sh.
set -e

_D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$(git -C "$_D" rev-parse --show-toplevel)/lib/ui.sh"
# shellcheck source=/dev/null
. "$_D/steps.sh"

step_mise_install
