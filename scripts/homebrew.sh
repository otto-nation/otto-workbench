#!/usr/bin/env bash
# Homebrew formula management for otto-workbench
# Updates and deploys the formula to otto-nation/homebrew-tap

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
source "$SCRIPT_DIR/common.sh"

readonly REPO="${GITHUB_ORG}/${GITHUB_REPO}"
readonly TAP_REPO="otto-nation/homebrew-tap"
readonly TAP_URL="https://github.com/${TAP_REPO}.git"
readonly FORMULA_FILE="Formula/${APP_NAME}.rb"

# Calculate SHA256 for a remote file
get_remote_sha256() {
    local url="$1"
    local temp_file

    temp_file=$(mktemp)
    # Expand now — temp_file is set on the preceding line
    # shellcheck disable=SC2064
    trap "rm -f '$temp_file'" RETURN

    if ! download_file "$url" "$temp_file" false; then
        print_error "Failed to download $url"
        return 1
    fi

    shasum -a 256 "$temp_file" | cut -d' ' -f1
}

# Update formula with version and checksum
cmd_update() {
    local version=""
    local checksum=""

    while [[ $# -gt 0 ]]; do
        case $1 in
            -v|--version) version="$2"; shift 2 ;;
            -s|--sha256) checksum="$2"; shift 2 ;;
            -h|--help)
                echo "Usage: $0 update -v <version> [-s <sha256>]"
                echo "Update Homebrew formula with checksum for a release."
                echo "If --sha256 is omitted, downloads the tarball to compute it."
                return 0
                ;;
            *) print_error "Unknown option: $1"; return 1 ;;
        esac
    done

    if [[ -z "$version" ]]; then
        print_error "Version is required. Use -v or --version"
        return 1
    fi

    local project_root
    project_root=$(get_project_root)
    local version_clean="${version#v}"

    print_status "Updating Homebrew formula for version $version..."

    if [[ -z "$checksum" ]]; then
        local tarball_url="https://github.com/$REPO/releases/download/$version/$APP_NAME-$version_clean.tar.gz"
        print_status "Calculating SHA256 for tarball..."
        checksum=$(get_remote_sha256 "$tarball_url")
    fi

    local formula_path="$project_root/$FORMULA_FILE"
    if [[ ! -f "$formula_path" ]]; then
        print_error "Formula file not found: $formula_path"
        return 1
    fi

    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/version \".*\"/version \"$version_clean\"/" "$formula_path"
        sed -i '' "s|/releases/download/[^/]*/[^\"]*|/releases/download/$version/$APP_NAME-$version_clean.tar.gz|" "$formula_path"
        sed -i '' "s/sha256 \".*\"/sha256 \"$checksum\"/" "$formula_path"
    else
        sed -i "s/version \".*\"/version \"$version_clean\"/" "$formula_path"
        sed -i "s|/releases/download/[^/]*/[^\"]*|/releases/download/$version/$APP_NAME-$version_clean.tar.gz|" "$formula_path"
        sed -i "s/sha256 \".*\"/sha256 \"$checksum\"/" "$formula_path"
    fi

    print_success "Formula updated successfully"
    print_info "  SHA256: $checksum"
}

# Deploy formula to tap repository
cmd_deploy() {
    local version=""
    local token="${HOMEBREW_TAP_TOKEN:-${GITHUB_TOKEN:-}}"
    local dry_run=false

    while [[ $# -gt 0 ]]; do
        case $1 in
            -v|--version) version="$2"; shift 2 ;;
            -t|--token) token="$2"; shift 2 ;;
            --dry-run) dry_run=true; shift ;;
            -h|--help)
                echo "Usage: $0 deploy -v <version> [--dry-run]"
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

    local project_root
    project_root=$(get_project_root)

    local formula_source="$project_root/$FORMULA_FILE"
    if [[ ! -f "$formula_source" ]]; then
        print_error "Formula file not found: $formula_source"
        return 1
    fi

    print_status "Deploying formula for version $version to $TAP_REPO..."

    local tap_dir
    tap_dir=$(mktemp -d)
    # Expand now — tap_dir is set on the preceding line
    # shellcheck disable=SC2064
    trap "rm -rf '$tap_dir'" RETURN

    print_status "Cloning tap repository..."
    if [[ -n "$token" ]]; then
        git clone "https://x-access-token:${token}@github.com/${TAP_REPO}.git" "$tap_dir" 2>&1 | grep -v "x-access-token" || true
    else
        git clone "$TAP_URL" "$tap_dir"
    fi

    local formula_dest="$tap_dir/$FORMULA_FILE"
    mkdir -p "$(dirname "$formula_dest")"
    cp "$formula_source" "$formula_dest"

    cd "$tap_dir"
    git config user.name "GitHub Actions"
    git config user.email "actions@github.com"

    if git diff --quiet; then
        print_info "No changes to formula"
        return 0
    fi

    git add "$FORMULA_FILE"
    git commit -m "Update ${APP_NAME} to $version"

    if [[ "$dry_run" == true ]]; then
        print_warning "Dry run — would push:"
        git show --stat
        return 0
    fi

    print_status "Pushing to tap repository..."
    git push origin main

    print_success "Formula deployed successfully to $TAP_REPO"
}

# Main command dispatcher
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
