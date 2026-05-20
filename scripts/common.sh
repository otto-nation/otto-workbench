#!/usr/bin/env bash
# Common utilities for otto-workbench release scripts

# Get project root directory
get_project_root() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
    if cd "$script_dir/.."; then
        pwd
    else
        return 1
    fi
}

# Brand constants
export GITHUB_ORG="otto-nation"
export APP_NAME="otto-workbench"
export GITHUB_REPO="otto-workbench"

# Colors
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly CYAN='\033[0;36m'
readonly NC='\033[0m'

# Print functions
print_status() { echo -e "${BLUE}[*]${NC} $1"; }
print_success() { echo -e "${GREEN}[+]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error() { echo -e "${RED}[-]${NC} $1" >&2; }
print_info() { echo -e "${CYAN}[i]${NC} $1"; }

# Download file
download_file() {
    local url="$1"
    local output="$2"
    local show_progress="${3:-true}"

    if [[ "$show_progress" == "true" ]]; then
        curl -fsSL --progress-bar -o "$output" "$url"
    else
        curl -fsSL -o "$output" "$url"
    fi
}
