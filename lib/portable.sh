#!/usr/bin/env bash
# Machine readers that work on both userlands.
#
# GNU coreutils and BSD spell the same values differently — `stat` takes
# different format flags, and the load average comes from `/proc/loadavg` on one
# and `sysctl vm.loadavg` on the other. Each reader here tries both forms so no
# caller has to branch on the platform. For `stat` that is also enforced:
# nothing outside this module calls it with a format flag, and
# `bin/local/validate-stat-portability` fails the build when something does. A
# hand-rolled fallback prints a filesystem report before failing on GNU, which
# has already broken CI once; the header on `_stat_field` has the details.
#
# It has no dependencies, so a caller that has not loaded the facade can source
# it on its own:
#
# ```bash
# file_mtime PATH   # modification time, epoch seconds
# file_birth PATH   # birth time, epoch seconds (0 where the FS has none)
# file_mode  PATH   # permission bits, octal — e.g. 644
# load_average      # one-minute load average, e.g. 3.72
# ```
#
# Each prints nothing and returns 1 when neither form resolves the value, so
# callers that want a default supply it themselves:
#
# ```bash
# ts=$(file_mtime "$f") || ts=0
# ```

# _stat_field GNU_FORMAT BSD_FORMAT PATH — prints one stat field.
#
# The two forms are assigned separately rather than chained inside a single
# command substitution. A `$(A || B)` captures the stdout of *every* command
# inside it: GNU stat reads -f as --file-system, so `stat -f %m FILE` treats
# %m as a filename, exits non-zero, and still prints a filesystem report for
# FILE — which would be concatenated with the fallback's output.
#
# `--` guards paths that begin with a dash; both stat implementations honour it.
_stat_field() {
  local gnu_format="$1" bsd_format="$2" path="$3" value
  value=$(stat -c "$gnu_format" -- "$path" 2>/dev/null) \
    || value=$(stat -f "$bsd_format" -- "$path" 2>/dev/null) \
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

# load_average — the machine's one-minute load average, as the kernel spells it.
#
# A decimal string such as `3.72`: the raw reading, not a rounded one, so a
# caller decides for itself which way to round. Prints nothing and returns 1
# where neither source is readable.
#
# The two forms are assigned separately for the same reason `_stat_field` does
# it — a `$(A || B)` would concatenate any stdout the losing form produced.
# BSD's `sysctl -n vm.loadavg` answers `{ 1.85 2.05 2.13 }` and Linux's
# `/proc/loadavg` answers `0.52 0.58 0.59 1/234 1234`, so dropping a leading
# `{ ` leaves the one-minute figure first in both.
load_average() {
  local raw
  raw=$(sysctl -n vm.loadavg 2>/dev/null) \
    || raw=$(cat /proc/loadavg 2>/dev/null) \
    || return 1
  raw="${raw#\{ }"
  printf '%s' "${raw%% *}"
}
