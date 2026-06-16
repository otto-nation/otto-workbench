#!/usr/bin/env bash
# Interactive mise installer — delegates to step_mise_install in steps.sh.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKBENCH_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=/dev/null
. "$WORKBENCH_DIR/lib/ui.sh"
# shellcheck source=/dev/null
. "$SCRIPT_DIR/steps.sh"

step_mise_install
