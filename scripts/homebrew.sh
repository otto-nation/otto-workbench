#!/usr/bin/env bash
# Homebrew formula management for otto-nation packages.
# Updates local formulas and deploys them to otto-nation/homebrew-tap.
#
# Usage: homebrew.sh publish -v VERSION --app APP_NAME [-s SHA256] [--dry-run]
#        homebrew.sh update  -v VERSION --app APP_NAME [-s SHA256]
#        homebrew.sh deploy  -v VERSION --app APP_NAME [--dry-run]

set -euo pipefail

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
_SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
_PROJECT_ROOT="$(cd "$_SCRIPT_DIR/.." && pwd)"

# shellcheck source=../lib/output.sh
source "$_PROJECT_ROOT/lib/output.sh"

readonly REPO="otto-nation/otto-workbench"
readonly TAP_REPO="otto-nation/homebrew-tap"
readonly DEFAULT_APP="otto-workbench"

# _tarball_url APP VERSION — download URL for a release tarball.
# otto-workbench tags: v1.4.0; others: APP-v1.0.0.
_tarball_url() {
    local app="$1" version="$2"
    local tag_prefix="v"
    [[ "$app" != "otto-workbench" ]] && tag_prefix="${app}-v"
    echo "https://github.com/$REPO/releases/download/${tag_prefix}${version}/${app}-${version}.tar.gz"
}

_download_sha256() {
    local url="$1" temp_file
    temp_file=$(mktemp)
    # shellcheck disable=SC2064
    trap "rm -f '$temp_file'" RETURN
    if ! curl -fsSL -o "$temp_file" "$url"; then
        err "Failed to download $url"
        return 1
    fi
    shasum -a 256 "$temp_file" | cut -d' ' -f1
}

# ---------------------------------------------------------------------------
# Core logic (called by the public commands)
# ---------------------------------------------------------------------------

_parse_args() {
    local -n __version=$1 __app=$2 __checksum=$3 __token=$4 __dry_run=$5
    shift 5

    __version="" __app="$DEFAULT_APP" __checksum="" __dry_run=false
    __token="${HOMEBREW_TAP_TOKEN:-${GITHUB_TOKEN:-}}"

    while [[ $# -gt 0 ]]; do
        case $1 in
            -v|--version) __version="$2"; shift 2 ;;
            --app)        __app="$2"; shift 2 ;;
            -s|--sha256)  __checksum="$2"; shift 2 ;;
            -t|--token)   __token="$2"; shift 2 ;;
            --dry-run)    __dry_run=true; shift ;;
            -h|--help)    return 2 ;;
            *)            err "Unknown option: $1"; return 1 ;;
        esac
    done

    if [[ -z "$__version" ]]; then
        err "Version is required. Use -v or --version"
        return 1
    fi

    # Strip any tag prefix (v1.2.0, app-v1.2.0) to get a clean semver
    __version="${__version##*v}"
    return 0
}

_update_formula() {
    local version="$1" app_name="$2" checksum="$3"

    local formula_path="$_PROJECT_ROOT/Formula/${app_name}.rb"
    if [[ ! -f "$formula_path" ]]; then
        err "Formula file not found: $formula_path"
        return 1
    fi

    info "Updating formula for ${app_name} ${version}..."

    if [[ -z "$checksum" ]]; then
        local tarball_url
        tarball_url=$(_tarball_url "$app_name" "$version")
        info "Calculating SHA256 for tarball..."
        checksum=$(_download_sha256 "$tarball_url")
    fi

    local url_path
    url_path=$(_tarball_url "$app_name" "$version")
    url_path="${url_path#*releases/download/}"

    sed_i "s/version \".*\"/version \"$version\"/" "$formula_path"
    sed_i "s|/releases/download/[^/]*/[^\"]*|/releases/download/$url_path|" "$formula_path"
    sed_i "s/sha256 \".*\"/sha256 \"$checksum\"/" "$formula_path"

    success "Formula updated"
    info "SHA256: $checksum"
}

_deploy_formula() {
    local version="$1" app_name="$2" token="$3" dry_run="$4"

    local formula_file="Formula/${app_name}.rb"
    local formula_source="$_PROJECT_ROOT/$formula_file"
    if [[ ! -f "$formula_source" ]]; then
        err "Formula file not found: $formula_source"
        return 1
    fi

    info "Deploying ${app_name} formula for version $version to $TAP_REPO..."

    if [[ "$dry_run" == true ]]; then
        warn "Dry run — would update ${formula_file} in ${TAP_REPO}"
        return 0
    fi

    if [[ -z "$token" ]]; then
        err "GitHub token required. Set GITHUB_TOKEN or HOMEBREW_TAP_TOKEN"
        return 1
    fi

    local api_path="repos/${TAP_REPO}/contents/${formula_file}"
    local content
    content=$(base64 < "$formula_source" | tr -d '\n')
    local local_sha
    local_sha=$(git hash-object "$formula_source")

    # Retry loop — concurrent deploys to the same tap branch cause 409 conflicts
    local max_attempts=5 attempt=1 current_sha="" api_args=()
    local err_file
    err_file=$(mktemp)
    # shellcheck disable=SC2064
    trap "rm -f '$err_file'" RETURN

    while [[ $attempt -le $max_attempts ]]; do
        current_sha=""
        current_sha=$(GH_TOKEN="$token" gh api "$api_path" --jq '.sha' 2>/dev/null) || true

        if [[ -n "$current_sha" ]] && [[ "$current_sha" == "$local_sha" ]]; then
            info "No changes to formula"
            return 0
        fi

        api_args=(
            --method PUT
            -f "message=Update ${app_name} to $version"
            -f "content=$content"
            -f "branch=main"
        )
        [[ -n "$current_sha" ]] && api_args+=(-f "sha=$current_sha")

        if GH_TOKEN="$token" gh api "$api_path" "${api_args[@]}" --silent 2>"$err_file"; then
            success "Formula deployed to $TAP_REPO"
            return 0
        fi

        # Only retry on 409 (conflict from concurrent branch updates)
        if ! grep -q "HTTP 409" "$err_file"; then
            err "Deploy failed"
            cat "$err_file" >&2
            return 1
        fi

        if [[ $attempt -lt $max_attempts ]]; then
            warn "Conflict (attempt $attempt/$max_attempts), retrying in $(( attempt * 2 ))s..."
            sleep $(( attempt * 2 ))
        fi

        (( attempt++ ))
    done

    err "Deploy failed after $max_attempts attempts — persistent conflict on $TAP_REPO"
    return 1
}

# ---------------------------------------------------------------------------
# Public commands
# ---------------------------------------------------------------------------

cmd_publish() {
    local version app_name checksum token dry_run rc=0
    _parse_args version app_name checksum token dry_run "$@" || rc=$?
    [[ $rc -eq 2 ]] && { _cmd_help publish; return 0; }
    [[ $rc -ne 0 ]] && return 1
    _update_formula "$version" "$app_name" "$checksum"
    _deploy_formula "$version" "$app_name" "$token" "$dry_run"
}

cmd_update() {
    local version app_name checksum token dry_run rc=0
    _parse_args version app_name checksum token dry_run "$@" || rc=$?
    [[ $rc -eq 2 ]] && { _cmd_help update; return 0; }
    [[ $rc -ne 0 ]] && return 1
    _update_formula "$version" "$app_name" "$checksum"
}

cmd_deploy() {
    local version app_name checksum token dry_run rc=0
    _parse_args version app_name checksum token dry_run "$@" || rc=$?
    [[ $rc -eq 2 ]] && { _cmd_help deploy; return 0; }
    [[ $rc -ne 0 ]] && return 1
    _deploy_formula "$version" "$app_name" "$token" "$dry_run"
}

_cmd_help() {
    case "$1" in
        publish) echo "Usage: $0 publish -v <version> --app <name> [-s <sha256>] [--dry-run]" ;;
        update)  echo "Usage: $0 update -v <version> --app <name> [-s <sha256>]" ;;
        deploy)  echo "Usage: $0 deploy -v <version> --app <name> [--dry-run]" ;;
    esac
}

_usage() {
    echo "Usage: $0 <publish|update|deploy> [options]"
    echo "Run '$0 <command> --help' for details."
}

case "${1:-}" in
    publish) shift; cmd_publish "$@" ;;
    update)  shift; cmd_update "$@" ;;
    deploy)  shift; cmd_deploy "$@" ;;
    help|--help|-h) _usage ;;
    "")
        err "No command specified"
        _usage
        exit 1
        ;;
    *)
        err "Unknown command: $1"
        exit 1
        ;;
esac
