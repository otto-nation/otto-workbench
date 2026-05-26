#!/usr/bin/env bash
# Portable shell helpers for macOS/Linux compatibility.

# Portable in-place sed (macOS and Linux)
# shellcheck disable=SC2145
_sed_i() { sed -i.bak "$@" && rm -f "${@: -1}.bak"; }
