# Docker & Container Configuration

# ============================================================================
# Environment Setup
# ============================================================================

export DOCKER_HOST=unix://${HOME}/.colima/default/docker.sock
export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock
export TESTCONTAINERS_HOST_OVERRIDE=localhost

# ============================================================================
# Colima Management
# ============================================================================

# Lazy colima start - only check when docker command is used
docker() {
  if ! command docker info >/dev/null 2>&1; then
    echo "Starting Colima..."
    colima start --arch x86_64 --vm-type=vz --vz-rosetta --cpu 2 --memory 4
    docker context use colima
  fi
  command docker "$@"
}

# ============================================================================
# Docker Shortcuts
# ============================================================================

# Core commands
alias d='docker'
alias dc='docker compose'

# Container management
alias d-ps='docker ps'
alias d-psa='docker ps -a'
alias d-images='docker images'
alias d-exec='docker exec -it'
alias d-logs='docker logs -f'
alias d-stop-all='docker stop $(docker ps -q)'
alias d-clean='docker system prune -af --volumes'
