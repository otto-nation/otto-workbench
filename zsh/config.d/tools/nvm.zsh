# nvm — Node.js version manager (lazy-loaded)
#
# nvm's init script adds ~200ms to every shell startup when sourced eagerly.
# This snippet lazy-loads nvm: the real init is deferred until the first time
# nvm, node, npm, npx, or yarn is actually invoked.
#
# No-op if nvm is not installed.
#
# If you use fnm or mise instead of nvm, disable this snippet.
#
# Install:         brew install nvm  (follow the post-install instructions for NVM_DIR)
# Docs:            https://github.com/nvm-sh/nvm
# duplicate-check: [[:space:]]nvm\.sh

export NVM_DIR="$HOME/.nvm"

[[ -s "$NVM_DIR/nvm.sh" ]] || return 0

# Lazy loader — defers sourcing nvm.sh until first use.
# Each shim calls wb_nvm_load once, then calls through to the real binary.
#
# No leading underscore: Claude Code's shell snapshot leaves out
# single-underscore functions, so shims captured without their loader would
# fail with "command not found" (tests/zsh_function_names.bats).
wb_nvm_load() {
  unfunction nvm node npm npx yarn 2>/dev/null
  source "$NVM_DIR/nvm.sh"
  [[ -s "$NVM_DIR/bash_completion" ]] && source "$NVM_DIR/bash_completion"
}

nvm()  { wb_nvm_load; nvm  "$@"; }
node() { wb_nvm_load; node "$@"; }
npm()  { wb_nvm_load; npm  "$@"; }
npx()  { wb_nvm_load; npx  "$@"; }
yarn() { wb_nvm_load; yarn "$@"; }
