#!/usr/bin/env bash
# Runs the suite and records its status. Callers gate on this script's exit code.
set -uo pipefail

bash ./suite.sh > out.txt 2>&1
