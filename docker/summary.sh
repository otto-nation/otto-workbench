#!/bin/bash
# Post-install summary for the docker component.
# Sourced by install.sh after all components run — defines print_docker_summary().
# No top-level execution; safe to source without side effects.

# print_docker_summary [RUNTIME] — prints next steps for starting the Docker runtime.
# RUNTIME is optional; when omitted, the active runtime is detected from the docker.sock symlink.
print_docker_summary() {
  local runtime="${1:-}"

  # Detect from socket symlink when not passed directly (e.g. called from install.sh summary)
  if [[ -z "$runtime" ]]; then
    local socket_target
    socket_target=$(readlink "$DOCKER_RUN_DIR/docker.sock" 2>/dev/null || true)
    if [[ "$socket_target" == "$COLIMA_DIR"* ]]; then
      runtime="colima"
    elif [[ -n "$socket_target" ]]; then
      runtime="orbstack"
    fi
  fi

  echo
  echo -e "  ${CYAN}Docker${NC}"
  case "$runtime" in
    colima)
      echo -e "  ${DIM}  Start colima:  colima start${NC}"
      ;;
    orbstack)
      echo -e "  ${DIM}  Launch OrbStack from Applications to start the Docker daemon.${NC}"
      ;;
    *)
      echo -e "  ${DIM}  Start your runtime (colima start, or launch OrbStack).${NC}"
      ;;
  esac
}
