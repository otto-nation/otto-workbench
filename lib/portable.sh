#!/usr/bin/env bash
# Portable file-metadata readers — GNU coreutils and BSD stat spell the same
# fields with different flags, and hand-rolling the fallback has already broken
# CI once (see the header on _stat_field).
#
# Usage (from scripts that already source lib/ui.sh, or by sourcing this file
# directly — it has no dependencies):
#   file_mtime PATH   # modification time, epoch seconds
#   file_birth PATH   # birth time, epoch seconds (0 where the FS has none)
#   file_mode  PATH   # permission bits, octal — e.g. 644
#
# Each prints nothing and returns 1 when neither form resolves the field, so
# callers that want a default supply it themselves:
#   ts=$(file_mtime "$f") || ts=0

# _stat_field GNU_FORMAT BSD_FORMAT PATH — prints one stat field.
#
# The two forms are assigned separately rather than chained inside a single
# command substitution. A `$(A || B)` captures the stdout of *every* command
# inside it: GNU stat reads -f as --file-system, so `stat -f %m FILE` treats
# %m as a filename, exits non-zero, and still prints a filesystem report for
# FILE — which would be concatenated with the fallback's output.
_stat_field() {
  local gnu_format="$1" bsd_format="$2" path="$3" value
  value=$(stat -c "$gnu_format" "$path" 2>/dev/null) \
    || value=$(stat -f "$bsd_format" "$path" 2>/dev/null) \
    || return 1
  printf '%s' "$value"
}

# file_mtime PATH — modification time in epoch seconds.
file_mtime() {
  _stat_field '%Y' '%m' "$1"
}

# file_birth PATH — birth (creation) time in epoch seconds. Prints 0 on
# filesystems that do not record one; callers must treat 0 as "unknown".
file_birth() {
  _stat_field '%W' '%B' "$1"
}

# file_mode PATH — permission bits as an octal string, e.g. 644.
file_mode() {
  _stat_field '%a' '%Lp' "$1"
}
