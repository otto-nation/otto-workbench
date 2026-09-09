#!/usr/bin/env bash
# The state the fix report describes: the path is quoted (T-1) and the array
# expansion is guarded (T-2), so a session that resolves the cited commit and
# reads it finds the fixes the drafts claim rather than contradicting them.
set -euo pipefail

deploy() {
    local dest=$1
    shift
    local targets=("$@")

    cp -r . "$dest"
    if [[ ${#targets[@]} -gt 0 ]]; then
        printf 'deployed to %s\n' "${targets[@]}"
    fi
}

retry() {
    local attempts=$1
    shift
    local n=0
    until "$@"; do
        n=$((n + 1))
        [[ $n -ge $attempts ]] && return 1
        sleep 1
    done
    return 0
}

retry 3 deploy "/var/www/app" "$@"
