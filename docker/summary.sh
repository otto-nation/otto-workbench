#!/bin/bash
# Post-install summary for the docker component.
# Sourced by install.sh after all components run — defines print_docker_summary().
# No top-level execution; safe to source without side effects.

# print_docker_summary — prints next steps for starting the Docker runtime.
print_docker_summary() {
  echo
  echo -e "  ${CYAN}Docker${NC}"
  echo -e "  ${DIM}  Start your runtime:${NC}"
  echo -e "  ${DIM}  \$ colima start          ${NC}${DIM}# Colima${NC}"
  echo -e "  ${DIM}  Or launch OrbStack from Applications.${NC}"
}
