# GitHub CLI — export GITHUB_TOKEN for tools that need it
#
# Exports GITHUB_TOKEN from the gh CLI's stored credential (keyring or config).
# This makes the token available to mise, homebrew.sh, and other tools that
# read GITHUB_TOKEN from the environment. No-op if gh is not installed or
# not authenticated.
#
# Install:         brew install gh
# Docs:            https://cli.github.com
# duplicate-check: GITHUB_TOKEN
# requires-cmd:    gh

# Skip if already set (e.g. CI, or user override in ~/.env.local)
[[ -n "${GITHUB_TOKEN:-}" ]] && return 0

_gh_token="$(gh auth token 2>/dev/null)" || return 0
export GITHUB_TOKEN="$_gh_token"
unset _gh_token
