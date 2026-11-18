# Docker shell configuration — shared across all runtimes.
#
# Runtime-specific config (Colima lazy-start, OrbStack no-op, etc.) is loaded
# from <state root>/docker-aliases.zsh — a symlink written by
# docker/setup.sh pointing to docker/<runtime>/aliases.zsh in the workbench.
# No-op if that symlink does not exist (fresh machine before docker/setup.sh runs).
#
# To switch runtimes: re-run docker/setup.sh or 'otto-workbench sync'.
#
# DOCKER_HOST and TESTCONTAINERS_* live in zsh/env.sh workbench_env, so a
# non-interactive caller gets the same socket as an interactive shell.

# ============================================================================
# Runtime-specific config (lazy-start, vars, etc.)
# ============================================================================

# The state root is spelled inline rather than sourced from lib/constants.sh:
# WORKBENCH_DIR is not known at shell startup, and sourcing would add a file
# read to every shell. Keep this chain identical to the one in constants.sh —
# tests/workbench_roots.bats cross-validates them.
_wb_docker_aliases="${WORKBENCH_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/workbench}/docker-aliases.zsh"
[[ -f "$_wb_docker_aliases" ]] && source "$_wb_docker_aliases"
unset _wb_docker_aliases

# ============================================================================
# Docker shortcuts
# ============================================================================

alias d='docker'
alias dc='docker compose'

alias d-ps='docker ps'
alias d-psa='docker ps -a'
alias d-images='docker images'
alias d-exec='docker exec -it'
alias d-logs='docker logs -f'
alias d-stop-all='docker ps -q | xargs -r docker stop'
alias d-clean='docker system prune -af --volumes'
