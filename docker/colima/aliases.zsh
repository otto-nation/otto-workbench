# Colima-specific docker shell configuration.
# Sourced at shell startup via ~/.config/workbench/docker-aliases.zsh (symlink).
# Written by docker/setup.sh when Colima is selected as the docker runtime.
#
# Override any of these in ~/.env.local before this file is sourced.

: "${COLIMA_PROFILE:=default}"
: "${COLIMA_ARCH:=x86_64}"
: "${COLIMA_VM_TYPE:=vz}"
: "${COLIMA_ROSETTA:=true}"
: "${COLIMA_CPU:=4}"
: "${COLIMA_MEMORY:=8}"

# Lazy colima start — only spins up Colima when a docker command is first used.
# Overrides the bare 'docker' command; all other docker aliases call this wrapper.
docker() {
  if ! command docker info >/dev/null 2>&1; then
    local -a colima_args=(--arch "$COLIMA_ARCH" --vm-type="$COLIMA_VM_TYPE" --cpu "$COLIMA_CPU" --memory "$COLIMA_MEMORY")
    [[ "$COLIMA_ROSETTA" == "true" ]] && colima_args+=(--vz-rosetta)

    if colima status &>/dev/null; then
      # VM is running but socket is stale (common after sleep/wake) — full restart needed.
      echo "Restarting Colima (stale socket)..."
      colima stop
      colima start "${colima_args[@]}"
    else
      echo "Starting Colima..."
      colima start "${colima_args[@]}"
    fi
    command docker context use colima
  fi
  command docker "$@"
}
