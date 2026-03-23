#!/bin/bash
# Task runner setup — manages go-task installation and global Taskfile symlinks.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../lib/ui.sh"
  unset _D
fi

# ─── Steps ────────────────────────────────────────────────────────────────────

# step_task_install — prompts to install go-task if not already present.
# Uses Homebrew on macOS, apt on Debian/Ubuntu, or prints a manual install URL otherwise.
# No-op if task is already installed. This is an install-time step — not called by sync_task.
step_task_install() {
  command -v task >/dev/null 2>&1 && return
  warn "Task (task runner) is not installed"
  printf "  Install it? [Y/n] "
  read -n 1 -r REPLY
  echo
  [[ "$REPLY" =~ ^[Nn]$ ]] && return

  if [[ "$OSTYPE" == "darwin"* ]]; then
    info "Installing task via Homebrew..."
    brew install go-task/tap/go-task
  elif command -v apt-get >/dev/null 2>&1; then
    if ! command -v curl >/dev/null 2>&1; then
      err "curl is required to install task. Install curl first: sudo apt-get install curl"
      return 1
    fi
    info "Installing task via apt..."
    sudo sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b /usr/local/bin
  else
    err "Unable to auto-install. See: https://taskfile.dev/installation/"
  fi
}

# step_task_symlinks — symlinks Taskfile.global.yml and lib/ into ~/.config/task/.
step_task_symlinks() {
  mkdir -p "$TASK_CONFIG_DIR"
  install_symlink "$TASKFILE_SRC"  "$TASK_CONFIG_DIR/Taskfile.yml"
  install_symlink "$LIB_SRC_DIR"   "$TASK_CONFIG_DIR/lib"
}

# sync_task — re-symlinks Taskfile and lib; safe to run non-interactively.
# Does not run step_task_install — installation is a one-time interactive operation.
sync_task() {
  echo; info "global Taskfile + lib → $TASK_CONFIG_DIR/"
  step_task_symlinks
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Task setup${NC}\n"

  echo; info "Task runner"
  step_task_install

  sync_task

  echo
  success "Task setup complete!"
fi
