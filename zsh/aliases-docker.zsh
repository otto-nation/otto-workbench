# Docker & Container Configuration

# ============================================================================
# Colima Configuration
# Override any of these in ~/.env.local before this file is sourced.
# ============================================================================

: "${COLIMA_PROFILE:=default}"
: "${COLIMA_ARCH:=x86_64}"
: "${COLIMA_VM_TYPE:=vz}"
: "${COLIMA_ROSETTA:=true}"
: "${COLIMA_CPU:=2}"
: "${COLIMA_MEMORY:=4}"

# ============================================================================
# Environment Setup
# ============================================================================

export DOCKER_HOST="unix://${HOME}/.colima/${COLIMA_PROFILE}/docker.sock"
export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock
export TESTCONTAINERS_HOST_OVERRIDE=localhost

# ============================================================================
# Colima Management
# ============================================================================

# Lazy colima start - only check when docker command is used
docker() {
  if ! command docker info >/dev/null 2>&1; then
    echo "Starting Colima..."
    local -a colima_args=(--arch "$COLIMA_ARCH" --vm-type="$COLIMA_VM_TYPE" --cpu "$COLIMA_CPU" --memory "$COLIMA_MEMORY")
    [[ "$COLIMA_ROSETTA" == "true" ]] && colima_args+=(--vz-rosetta)
    colima start "${colima_args[@]}"
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
