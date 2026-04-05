#!/usr/bin/env bash
# Post-install summary for the docker component.
# Sourced by install.sh after all components run — defines print_docker_summary().
# No top-level execution; safe to source without side effects.

# print_docker_summary [RUNTIME] — prints next steps for starting the Docker runtime.
# RUNTIME is optional; when omitted, the active runtime is detected from the docker.sock symlink.
print_docker_summary() {
  local runtime="${1:-}"

  # Detect from socket symlink when not passed directly (e.g. called from install.sh summary).
  # _docker_detect_runtime is defined in docker/steps.sh, which is sourced before summary files
  # in both install.sh and otto-workbench sync flows.
  if [[ -z "$runtime" ]]; then
    runtime=$(_docker_detect_runtime)
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
