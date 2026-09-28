#!/usr/bin/env bash
# Tiny stand-in for a test runner. SUITE_EXIT selects the result.
set -euo pipefail
echo "running suite"
exit "${SUITE_EXIT:-0}"
