# Colima-specific docker shell configuration.
# Sourced at shell startup via $WORKBENCH_STATE_DIR/docker-aliases.zsh (symlink);
# the state root is defined in lib/roots.sh — see docs/libraries.md.
# Written by docker/setup.sh when Colima is selected as the docker runtime.
#
# Override any of these in ~/.env.local before this file is sourced.

: "${COLIMA_PROFILE:=default}"
: "${COLIMA_VM_TYPE:=vz}"
: "${COLIMA_ROSETTA:=true}"
: "${COLIMA_CPU:=4}"
: "${COLIMA_MEMORY:=8}"

# Architecture is detected, not defaulted. docker/registry.yml deliberately
# gives COLIMA_ARCH no default: a registry default is rendered into ~/.env.local
# as a live `export`, which would pin every machine to one architecture and win
# over anything set here. Colima wants aarch64/x86_64; uname reports arm64 on
# Apple Silicon. Override in ~/.env.local (below ENV-END) to force a value.
if [[ -z "${COLIMA_ARCH:-}" ]]; then
  case "$(uname -m)" in
    arm64 | aarch64) COLIMA_ARCH=aarch64 ;;
    *) COLIMA_ARCH=x86_64 ;;
  esac
fi

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
