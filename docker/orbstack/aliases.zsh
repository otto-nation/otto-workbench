# OrbStack-specific docker shell configuration.
# Sourced at shell startup via ~/.config/workbench/docker-aliases.zsh (symlink).
# Written by docker/setup.sh when OrbStack is selected as the docker runtime.
#
# OrbStack manages /var/run/docker.sock and the docker context automatically.
# No lazy-start wrapper needed — the OrbStack menu-bar app handles the daemon.
