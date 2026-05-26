#!/usr/bin/env bash
# Homebrew formula management for otto-nation packages.
# Updates and deploys formulas to otto-nation/homebrew-tap.
#
# Usage: homebrew.sh update -v VERSION --app APP_NAME [-s SHA256]
#        homebrew.sh deploy -v VERSION --app APP_NAME [--dry-run]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
source "$SCRIPT_DIR/common.sh"

readonly REPO="${GITHUB_ORG}/${GITHUB_REPO}"
readonly TAP_REPO="otto-nation/homebrew-tap"
readonly TAP_URL="https://github.com/${TAP_REPO}.git"

# shellcheck source=lib/portability.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/lib/portability.sh"

# _tarball_url APP_NAME VERSION — returns the download URL for a release tarball.
# otto-workbench uses tags like v1.4.0, claude-review uses claude-review-v1.0.0.
_tarball_url() {
    local app="$1" version="$2"
    if [[ "$app" == "otto-workbench" ]]; then
        echo "https://github.com/$REPO/releases/download/v${version}/${app}-${version}.tar.gz"
    else
        echo "https://github.com/$REPO/releases/download/${app}-v${version}/${app}-${version}.tar.gz"
    fi
}

get_remote_sha256() {
    local url="$1"
    local temp_file

    temp_file=$(mktemp)
    # shellcheck disable=SC2064
    trap "rm -f '$temp_file'" RETURN

    if ! download_file "$url" "$temp_file" false; then
        print_error "Failed to download $url"
        return 1
    fi

    shasum -a 256 "$temp_file" | cut -d' ' -f1
}

cmd_update() {
    # shellcheck disable=SC2153  # APP_NAME from common.sh
    local version="" checksum="" app_name="${APP_NAME}"

    while [[ $# -gt 0 ]]; do
        case $1 in
            -v|--version) version="$2"; shift 2 ;;
            -s|--sha256) checksum="$2"; shift 2 ;;
            --app) app_name="$2"; shift 2 ;;
            -h|--help)
                echo "Usage: $0 update -v <version> --app <name> [-s <sha256>]"
                echo "Update Homebrew formula with checksum for a release."
                return 0
                ;;
            *) print_error "Unknown option: $1"; return 1 ;;
        esac
    done

    if [[ -z "$version" ]]; then
        print_error "Version is required. Use -v or --version"
        return 1
    fi

    local project_root formula_file
    project_root=$(get_project_root)
    formula_file="Formula/${app_name}.rb"
    local version_clean="${version##*v}"

    print_status "Updating Homebrew formula for ${app_name} version $version_clean..."

    if [[ -z "$checksum" ]]; then
        local tarball_url
        tarball_url=$(_tarball_url "$app_name" "$version_clean")
        print_status "Calculating SHA256 for tarball..."
        checksum=$(get_remote_sha256 "$tarball_url")
    fi

    local formula_path="$project_root/$formula_file"
    if [[ ! -f "$formula_path" ]]; then
        print_error "Formula file not found: $formula_path"
        return 1
    fi

    local url_path
    url_path=$(_tarball_url "$app_name" "$version_clean")
    url_path="${url_path#*releases/download/}"

    _sed_i "s/version \".*\"/version \"$version_clean\"/" "$formula_path"
    _sed_i "s|/releases/download/[^/]*/[^\"]*|/releases/download/$url_path|" "$formula_path"
    _sed_i "s/sha256 \".*\"/sha256 \"$checksum\"/" "$formula_path"

    print_success "Formula updated successfully"
    print_info "  SHA256: $checksum"
}

cmd_deploy() {
    local version="" app_name="${APP_NAME}"
    local token="${HOMEBREW_TAP_TOKEN:-${GITHUB_TOKEN:-}}"
    local dry_run=false

    while [[ $# -gt 0 ]]; do
        case $1 in
            -v|--version) version="$2"; shift 2 ;;
            --app) app_name="$2"; shift 2 ;;
            -t|--token) token="$2"; shift 2 ;;
            --dry-run) dry_run=true; shift ;;
            -h|--help)
                echo "Usage: $0 deploy -v <version> --app <name> [--dry-run]"
                echo "Deploy Homebrew formula to tap repository."
                return 0
                ;;
            *) print_error "Unknown option: $1"; return 1 ;;
        esac
    done

    if [[ -z "$version" ]]; then
        print_error "Version is required. Use -v or --version"
        return 1
    fi

    if [[ -z "$token" ]] && [[ "$dry_run" == false ]]; then
        print_error "GitHub token required. Set GITHUB_TOKEN or HOMEBREW_TAP_TOKEN"
        return 1
    fi

    local project_root formula_file
    project_root=$(get_project_root)
    formula_file="Formula/${app_name}.rb"

    local formula_source="$project_root/$formula_file"
    if [[ ! -f "$formula_source" ]]; then
        print_error "Formula file not found: $formula_source"
        return 1
    fi

    print_status "Deploying ${app_name} formula for version $version to $TAP_REPO..."

    local tap_dir
    tap_dir=$(mktemp -d)
    # shellcheck disable=SC2064
    trap "rm -rf '$tap_dir'" RETURN

    print_status "Cloning tap repository..."
    if [[ -n "$token" ]]; then
        git clone "https://x-access-token:${token}@github.com/${TAP_REPO}.git" "$tap_dir" 2>&1 | grep -v "x-access-token" || true
    else
        git clone "$TAP_URL" "$tap_dir"
    fi

    local formula_dest="$tap_dir/$formula_file"
    mkdir -p "$(dirname "$formula_dest")"
    cp "$formula_source" "$formula_dest"

    cd "$tap_dir"
    git config user.name "GitHub Actions"
    git config user.email "actions@github.com"

    if git diff --quiet; then
        print_info "No changes to formula"
        return 0
    fi

    git add "$formula_file"
    git commit -m "Update ${app_name} to $version"

    if [[ "$dry_run" == true ]]; then
        print_warning "Dry run — would push:"
        git show --stat
        return 0
    fi

    print_status "Pushing to tap repository..."
    git push origin main

    print_success "Formula deployed successfully to $TAP_REPO"
}

case "${1:-}" in
    update) shift; cmd_update "$@" ;;
    deploy) shift; cmd_deploy "$@" ;;
    help|--help|-h)
        echo "Usage: $0 <update|deploy> [options]"
        echo "Run '$0 <command> --help' for details."
        ;;
    "")
        print_error "No command specified"
        echo "Usage: $0 <update|deploy> [options]"
        exit 1
        ;;
    *)
        print_error "Unknown command: $1"
        exit 1
        ;;
esac
