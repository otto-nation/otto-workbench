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

  summary_section "Docker"

  if docker info >/dev/null 2>&1; then
    summary_ok "running ${DIM}(${runtime:-unknown runtime})${NC}"
  else
    local _hint="start your runtime"
    case "$runtime" in
      colima)   _hint="colima start" ;;
      orbstack) _hint="launch OrbStack from Applications" ;;
    esac
    summary_warn "not running — ${DIM}${_hint}${NC}"
  fi
}
