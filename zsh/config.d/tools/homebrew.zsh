# Homebrew — macOS package manager PATH setup
#
# Adds Homebrew to PATH and exports BREW_PREFIX for use by other snippets.
# Must load before any tool installed via Homebrew (pyenv, nvm, etc.) because
# their binaries need to be on PATH before their init scripts run.
#
# Supports both Apple Silicon (/opt/homebrew) and Intel (/usr/local) Macs.
# No-op if Homebrew is not installed.
#
# Install: https://brew.sh

if [[ -x /opt/homebrew/bin/brew ]]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -x /usr/local/bin/brew ]]; then
  eval "$(/usr/local/bin/brew shellenv)"
else
  return 0
fi

# BREW_PREFIX is set by `brew shellenv` above as HOMEBREW_PREFIX.
# Re-export as BREW_PREFIX for convenience in other snippets and scripts.
export BREW_PREFIX="${HOMEBREW_PREFIX}"
